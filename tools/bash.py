"""Bash 工具，在本地 shell 中执行命令并流式返回输出。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import TYPE_CHECKING, Any, AsyncIterator

from permissions.rules import (
    AllowDecision,
    AskDecision,
    DenyDecision,
    PermissionDecision,
    PermissionRule,
)
from tools.base import BaseTool, ToolUseContext
from tools.bash_parse import parse_bash_command

if TYPE_CHECKING:
    from permissions.manager import PermissionContext

_DEFAULT_TIMEOUT = 30


def _resolve_shell() -> list[str]:
    """定位实际用来执行命令的 shell。

    Windows 上必须选真正的 bash（Git Bash），不能是 WindowsApps 下会转发到 WSL
    的 `bash.exe`，否则命令的执行语法（POSIX bash）与权限层的解析语法一致这个
    前提会被破坏。优先级：Git 默认安装路径 → PATH 里名字确切是 bash 且不在
    WindowsApps 下 → 都找不到则退回 powershell 并接受解析/执行语法不一致的
    已知限制（此时子命令级权限判定失效，工具级规则仍生效）。
    """
    if sys.platform != "win32":
        return ["bash", "-c"]

    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if os.path.isfile(candidate):
            return [candidate, "-c"]

    which = shutil.which("bash")
    if which and "WindowsApps" not in which:
        return [which, "-c"]

    return ["powershell", "-Command"]


_SHELL = _resolve_shell()


class BashTool(BaseTool):
    """在本地 shell 中执行命令，逐行流式 yield 输出。

    Windows 上优先选 Git Bash 执行，让权限层（用 bashlex 按 POSIX bash 语法
    解析）与执行层使用同一套语法，避免 parser differential 类漏洞。找不到
    真正的 bash 时退回 PowerShell 并接受子命令级权限判定失效的已知限制。
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "Bash"

    @property
    def description(self) -> str:
        return (
            "在本地 shell 中执行命令。"
            "适用于文件操作、运行脚本、查看目录结构等任务。"
            "stdout 和 stderr 合并返回。"
            f"超时限制为 {self._timeout} 秒。"
            f"当前 shell: {_SHELL[0]}。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令。",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"可选，覆盖默认超时（秒），最大 {_DEFAULT_TIMEOUT * 2}。",
                },
            },
            "required": ["command"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision | None:
        """按 Bash(cmd) / Bash(cmd:*) 规则做子命令级权限判定，fail-closed。

        流程：
          1. 用 bashlex 解析命令，失败 / too-complex → Ask（不放行）
          2. 对每条子命令的 argv，按 deny → ask → allow 顺序匹配 content 规则
          3. deny 命中即 Deny；ask 命中即 Ask；所有子命令都命中 allow 才 Allow
          4. 否则 return None，交由六步流水线后续步骤（大概率转 ask）

        规则格式对齐 TS 版兼容语法：
          - `Bash(git status)`     — exact 匹配整条 argv 字符串
          - `Bash(git:*)`          — prefix 匹配（等价于 argv[0] == "git"）
          - `Bash(git status:*)`   — prefix 匹配（前缀 "git status"）

        Args:
            tool_input: 至少含 command 字段。
            context: 权限上下文，读取其 allow/deny/ask 规则里 rule_content
                非 None 的部分。

        Returns:
            明确决策（Allow / Deny / Ask）或 None（passthrough）。
        """
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None  # 让 validate_input 处理参数校验

        parsed = parse_bash_command(command)
        if parsed.kind == "too-complex":
            # fail-closed：命令含无法安全解析的结构（$()、变量、heredoc 等）
            # 一律转 ask，不允许因"看起来像 git 命令"就放行。
            return AskDecision(
                reason=f"命令无法安全静态分析（{parsed.reason}），需要用户确认",
            )

        # 分类三种规则集里 content 非 None 的部分（工具级规则由六步流水线的
        # 步骤 1/2/6 处理，这里只关心子命令粒度）
        deny_contents = _content_rules(context.deny_rules, self.name)
        ask_contents = _content_rules(context.ask_rules, self.name)
        allow_contents = _content_rules(context.allow_rules, self.name)

        # 每条子命令都要独立过 deny / ask 检查；只有全部子命令都命中 allow
        # 才能整体 allow。任何一条子命令 deny/ask 命中，整个命令跟着降级。
        all_allowed = True
        for argv in parsed.argvs:
            cmd_str = " ".join(argv)

            deny_match = _match_content_rules(cmd_str, deny_contents)
            if deny_match is not None:
                return DenyDecision(
                    reason=f"子命令 `{cmd_str}` 匹配 deny 规则 `{deny_match.value.rule_content}`",
                )

            ask_match = _match_content_rules(cmd_str, ask_contents)
            if ask_match is not None:
                return AskDecision(
                    reason=f"子命令 `{cmd_str}` 匹配 ask 规则 `{ask_match.value.rule_content}`",
                    rule=ask_match,
                )

            if _match_content_rules(cmd_str, allow_contents) is None:
                all_allowed = False

        if allow_contents and all_allowed:
            return AllowDecision(
                reason=f"命令的所有子命令均匹配 allow 规则（共 {len(parsed.argvs)} 条）",
            )

        # passthrough：允许上层流水线继续决定（default 模式下会落到 ask）
        return None

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """执行 shell 命令，逐行 yield 输出。

        Args:
            tool_input: 包含 command（必填）和可选 timeout 的参数字典。
            context: 工具执行上下文（预留给权限扩展）。

        Yields:
            命令输出的文本片段。超时或异常时 yield 错误描述。
        """
        command = tool_input["command"]
        timeout = min(tool_input.get("timeout", self._timeout), _DEFAULT_TIMEOUT * 2)

        try:
            process = await asyncio.create_subprocess_exec(
                *_SHELL,
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None

            async for line in process.stdout:
                yield line.decode(errors="replace")

            await asyncio.wait_for(process.wait(), timeout=timeout)

            if process.returncode and process.returncode != 0:
                yield f"\n[退出码: {process.returncode}]\n"

        except asyncio.TimeoutError:
            yield f"[命令超时：{timeout} 秒后终止]\n"

        except FileNotFoundError:
            yield f"[Shell 未找到：{_SHELL[0]}]\n"


# ── 子命令级规则匹配辅助 ──────────────────────────────────────────────────


def _content_rules(
    rules: tuple[PermissionRule, ...],
    tool_name: str,
) -> tuple[PermissionRule, ...]:
    """从规则集里筛出针对指定工具、且 rule_content 非空的规则。

    工具级规则（rule_content is None）由 PermissionManager 流水线本身处理，
    这里只关心子命令粒度的 content 规则，比如 `Bash(git status)` 和 `Bash(git:*)`。
    """
    return tuple(
        r for r in rules
        if r.value.tool_name == tool_name and r.value.rule_content is not None
    )


def _match_content_rules(
    command_str: str,
    rules: tuple[PermissionRule, ...],
) -> PermissionRule | None:
    """检查命令字符串是否命中给定规则集中的任一条，返回命中的规则。

    匹配规则（对齐 TS 版 shellRuleMatching.ts 的兼容语法）：
      - 规则以 `:*` 结尾 → prefix 匹配（去掉 `:*` 后作为前缀，
        `Bash(git:*)` 匹配 `git`、`git status`、`git commit -m x`）
      - 其余情况 → exact 匹配整条命令字符串

    Args:
        command_str: 已 join 的 argv，形如 "git status"。
        rules: 待匹配的规则集合，通常来自 _content_rules() 的输出。

    Returns:
        第一条命中的规则；无命中时为 None。
    """
    for rule in rules:
        content = rule.value.rule_content
        if content is None:  # _content_rules 已经过滤，理论不会命中
            continue
        if content.endswith(":*"):
            prefix = content[:-2]
            # 前缀匹配需要边界：允许"命令完全等于前缀"或"命令以前缀+空格开头"，
            # 避免 `Bash(git:*)` 意外命中 `github-cli status` 之类的命令名前缀重叠
            if command_str == prefix or command_str.startswith(prefix + " "):
                return rule
        else:
            if command_str == content:
                return rule
    return None
