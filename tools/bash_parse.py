"""从 bash 命令字符串提取子命令 argv 列表，供权限层做子命令级规则匹配。

设计原则：**FAIL-CLOSED**。对应 claude-code TS 源码 `src/utils/bash/ast.ts`
里的核心哲学："we never interpret structure we don't understand"。

- 遇到显式允许的 AST 节点：正常展开、提取每条 simple command 的 argv[]
- 遇到任何一个不认识的节点结构（命令替换 $()、进程替换、heredoc、
  函数定义、算术展开等）：整个字符串判定为 too-complex
- bashlex 抛异常（NotImplementedError / ParsingError）：判定为 too-complex

上层的权限判定收到 too-complex 时**必须**升级为 ask 或 deny，绝不能因为
"看起来像 git 命令"就放行——那正是六个对抗 Agent 反复警告的、比"完全不做
子命令匹配"更危险的假安全边界。

不复刻的部分（相对 TS 版 `ast.ts` 2679 行 + `bashParser.ts` 4432 行）：
- 变量赋值追踪 + 占位符替换（`x=rm; $x -rf /` 类）：本模块把 $VAR 引用
  也判为 too-complex，不做流依赖分析。
- 环境变量白名单（$HOME/$PWD 等安全展开）：一律 too-complex。
- 引号剥离细节、$IFS 注入检测等：不实现。
这些简化都朝着 fail-closed 方向偏——放弃精度换 ask，不会朝允许方向漏判。

对外仅暴露：
  ParseResult — dataclass，字段 kind、argvs、reason
  parse_bash_command — 主入口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import bashlex
import bashlex.errors


# ── 结果类型 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParseResult:
    """bash 命令解析结果。

    Attributes:
        kind: "simple" 表示成功提取出所有子命令 argv；"too-complex" 表示遇到
            不认识的语法结构或解析失败，权限层必须升级为 ask/deny。
        argvs: 只有 kind == "simple" 时才有意义。每个元素是一条 simple command
            的参数列表，argv[0] 是命令名，其余是参数（保留引号剥离后的字面值）。
        reason: 只有 kind == "too-complex" 时才有意义。给权限层展示的原因，
            用于生成 ask 提示文案。
    """

    kind: Literal["simple", "too-complex"]
    argvs: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    reason: str = ""


# ── 允许列表（显式白名单，其他一律 too-complex） ──────────────────────────


# 结构性节点：它们本身没有可执行语义，只是命令的组合器，递归下去继续找命令
_STRUCTURAL_KINDS = frozenset({
    "list",       # a && b || c，或 a; b; c
    "pipeline",   # a | b
    "operator",   # &&/||/; 等分隔符本身
    "pipe",       # | 本身
    "reservedword",  # 出现在 list/pipeline 里的关键字（这里当叶子跳过）
})

# 只允许 argv 的组成字符：普通词、赋值前缀（如 KEY=val）、重定向（跳过）
_COMMAND_CHILD_ALLOWED = frozenset({
    "word",         # argv 的正常组成
    "assignment",   # 命令前缀的 VAR=val（先记录再判定）
    "redirect",     # >、<、>> 等，跳过重定向目标不参与 argv
})


# ── 内部工具 ──────────────────────────────────────────────────────────────


def _extract_word(node) -> str | None:
    """从一个 word 节点提取字面值；含变量/命令替换等结构则返回 None。

    Args:
        node: bashlex 的 word 节点（node.kind == "word"）。

    Returns:
        安全的字面字符串；只要包含任何一个非字面 part（parameter/command
        substitution 等），返回 None 表示"这个 argv 元素不可信"。
    """
    parts = getattr(node, "parts", None) or []
    for p in parts:
        # 有 parts 说明 word 里存在结构化片段（如 $VAR、$()、``、$'...'）
        # 保守起见：只要有任何一个非字面结构，整个词都不可信
        if p.kind != "tilde":  # tilde 展开是路径开头的 ~，视为字面
            return None
    return getattr(node, "word", None)


def _extract_argv_from_command(node) -> ParseResult | tuple[str, ...]:
    """从一个 command 节点提取 argv 元组，失败时返回 too-complex ParseResult。

    Args:
        node: bashlex 的 command 节点（node.kind == "command"）。

    Returns:
        成功：argv 元组；失败：ParseResult(kind="too-complex", reason=...)。
    """
    argv: list[str] = []
    for child in node.parts:
        kind = child.kind
        if kind not in _COMMAND_CHILD_ALLOWED:
            return ParseResult(
                kind="too-complex",
                reason=f"命令内含无法安全解析的结构：{kind}",
            )
        if kind == "redirect":
            # heredoc（<<EOF ... EOF）里的内容会成为下游解释器（如 `bash`、
            # `python -c`）的 stdin，静态权限规则完全看不见 heredoc 内部的
            # 危险动作。有 heredoc 附着的重定向一律 fail-closed。
            if getattr(child, "heredoc", None) is not None:
                return ParseResult(
                    kind="too-complex",
                    reason="命令含 heredoc 重定向（<<EOF），无法静态判断执行内容",
                )
            # 普通重定向目标不进入 argv；目标本身若含 $() 等结构会在 word
            # 层通过 _extract_word 被 fail-closed 拦截，这里不重复检查。
            continue
        if kind == "assignment":
            # KEY=val 类型的命令前缀（如 `a=1 rm -rf /`）本身不是 argv[0]，
            # 但存在这种前缀说明命令语义里有环境变量注入，保守判定 too-complex。
            # 也避免 `IFS=$'\n' cmd` 这类 $IFS 注入初级形态。
            return ParseResult(
                kind="too-complex",
                reason="命令包含内联环境变量赋值前缀",
            )
        # kind == "word"
        literal = _extract_word(child)
        if literal is None:
            return ParseResult(
                kind="too-complex",
                reason="命令参数中含变量引用或命令替换",
            )
        argv.append(literal)

    if not argv:
        return ParseResult(kind="too-complex", reason="命令没有可提取的 argv")

    return tuple(argv)


def _walk(node, out_argvs: list[tuple[str, ...]]) -> ParseResult | None:
    """递归遍历 AST，遇到 command 就把 argv 追加到 out_argvs。

    返回 None 表示这条分支处理成功；返回 ParseResult(too-complex) 表示遇到
    了不允许的节点，调用方必须立即向上传播（fail-closed）。
    """
    kind = node.kind

    if kind == "command":
        result = _extract_argv_from_command(node)
        if isinstance(result, ParseResult):
            return result
        out_argvs.append(result)
        return None

    if kind in _STRUCTURAL_KINDS:
        for child in getattr(node, "parts", None) or []:
            r = _walk(child, out_argvs)
            if r is not None:
                return r
        return None

    # 其余节点全部不在允许列表内：commandsubstitution / processsubstitution /
    # heredoc / function / compound（$(...)、<()、<<EOF、function、{ ...; }、
    # (subshell)、if/for/while/until）——一律 fail-closed。
    return ParseResult(
        kind="too-complex",
        reason=f"命令包含无法安全解析的结构：{kind}",
    )


# ── 对外主入口 ────────────────────────────────────────────────────────────


def parse_bash_command(command: str) -> ParseResult:
    """解析 bash 命令字符串，尝试提取所有子命令的 argv。

    Args:
        command: 用户 / 模型传入的完整 bash 命令字符串。

    Returns:
        ParseResult：kind="simple" 时 argvs 包含所有子命令的 argv 元组；
        kind="too-complex" 时权限层必须升级为 ask 或 deny，不允许放行。
    """
    stripped = command.strip()
    if not stripped:
        return ParseResult(kind="too-complex", reason="空命令")

    try:
        trees = bashlex.parse(stripped)
    except bashlex.errors.ParsingError as exc:
        return ParseResult(kind="too-complex", reason=f"bash 语法错误：{exc}")
    except NotImplementedError as exc:
        # 算术展开 $((...))、数组字面量 arr=(...) 等 bashlex 未实现的语法
        return ParseResult(kind="too-complex", reason=f"未支持的 bash 语法：{exc}")
    except Exception as exc:  # noqa: BLE001
        # 任何未预期异常也 fail-closed，避免解析器自身 bug 变成安全漏洞
        return ParseResult(kind="too-complex", reason=f"解析异常：{exc}")

    argvs: list[tuple[str, ...]] = []
    for tree in trees:
        r = _walk(tree, argvs)
        if r is not None:
            return r

    if not argvs:
        return ParseResult(kind="too-complex", reason="未识别出任何子命令")

    return ParseResult(kind="simple", argvs=tuple(argvs))
