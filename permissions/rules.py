"""权限规则的类型定义与解析工具。

定义权限规则的数据结构，以及规则字符串的解析和序列化逻辑。
规则格式为 "ToolName" 或 "ToolName(content)"，
content 中的括号需转义，例如 "Bash(python -c \"print\\(1\\)\")"。

对外暴露：
  PermissionMode    — 权限模式枚举
  PermissionBehavior — 规则行为枚举
  RuleSource        — 规则来源枚举
  PermissionRuleValue — 规则值（工具名 + 可选内容）
  PermissionRule    — 完整规则（含来源和行为）
  parse_rule        — 将规则字符串解析为 PermissionRuleValue
  rule_to_string    — 将 PermissionRuleValue 序列化为字符串
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel


# ── 枚举类型 ──────────────────────────────────────────────────────────────


class PermissionMode(str, Enum):
    """权限模式，控制工具执行的整体审批策略。

    取值语义：
      default          — 逐个询问用户
      accept_edits     — 允许所有写操作，无需确认
      bypass           — 允许一切，跳过所有检查（沙箱/自动化场景）
      plan             — 计划模式，先展示再执行
      auto             — 由分类器自动决策（二期实现）
      dont_ask         — 拒绝所有需要询问的操作
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    BYPASS = "bypassPermissions"
    PLAN = "plan"
    AUTO = "auto"
    DONT_ASK = "dontAsk"


class PermissionBehavior(str, Enum):
    """规则行为，描述规则命中时的处置方式。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class RuleSource(str, Enum):
    """规则来源，标识规则从哪里加载，影响优先级和持久化方式。"""

    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    LOCAL_SETTINGS = "localSettings"
    CLI_ARG = "cliArg"
    SESSION = "session"


# ── 数据类型（Pydantic：规则会在配置文件和 API 边界传输） ──────────────────


class PermissionRuleValue(BaseModel):
    """权限规则的值，描述规则匹配的目标。

    对应规则字符串 "ToolName" 或 "ToolName(content)"。

    Attributes:
        tool_name: 规则适用的工具名称。
        rule_content: 规则的内容限定，None 表示匹配整个工具。
            例如 "Bash(npm install)" 中 rule_content 为 "npm install"。
    """

    tool_name: str
    rule_content: str | None = None


class PermissionRule(BaseModel):
    """完整的权限规则，包含值、来源和行为。

    Attributes:
        value: 规则匹配目标。
        source: 规则加载来源。
        behavior: 规则命中时的处置方式。
    """

    value: PermissionRuleValue
    source: RuleSource
    behavior: PermissionBehavior


# ── 决策结果类型 ───────────────────────────────────────────────────────────


class AllowDecision(BaseModel):
    """允许工具执行的决策。

    Attributes:
        reason: 允许原因的简要描述，用于日志和可观测性。
    """

    behavior: Literal["allow"] = "allow"
    reason: str = ""


class DenyDecision(BaseModel):
    """拒绝工具执行的决策。

    Attributes:
        reason: 拒绝原因，会返回给模型让其感知并自行决策。
    """

    behavior: Literal["deny"] = "deny"
    reason: str


class AskDecision(BaseModel):
    """需要询问用户的决策（交互模式下展示确认对话框）。

    Attributes:
        reason: 询问原因，用于生成提示文案。
        rule: 触发此决策的规则，供 UI 展示。
    """

    behavior: Literal["ask"] = "ask"
    reason: str
    rule: PermissionRule | None = None


PermissionDecision = AllowDecision | DenyDecision | AskDecision


# ── 规则解析 ──────────────────────────────────────────────────────────────


def _find_first_unescaped(s: str, char: str) -> int:
    """返回字符串中第一个未转义的指定字符的索引，未找到返回 -1。

    转义规则：字符前有奇数个反斜杠时视为已转义。

    Args:
        s: 待搜索的字符串。
        char: 单个字符。

    Returns:
        第一个未转义字符的索引，未找到时为 -1。
    """
    for i, c in enumerate(s):
        if c == char:
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                return i
    return -1


def _find_last_unescaped(s: str, char: str) -> int:
    """返回字符串中最后一个未转义的指定字符的索引，未找到返回 -1。

    Args:
        s: 待搜索的字符串。
        char: 单个字符。

    Returns:
        最后一个未转义字符的索引，未找到时为 -1。
    """
    for i in range(len(s) - 1, -1, -1):
        if s[i] == char:
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                return i
    return -1


def _unescape_content(content: str) -> str:
    """还原规则 content 中的转义字符。

    反转义顺序必须与转义顺序相反：先还原括号，再还原反斜杠。

    Args:
        content: 含转义字符的 content 字符串。

    Returns:
        还原后的原始字符串。
    """
    return content.replace(r"\(", "(").replace(r"\)", ")").replace("\\\\", "\\")


def _escape_content(content: str) -> str:
    """对规则 content 中的特殊字符进行转义。

    转义顺序必须固定：先转义反斜杠，再转义括号，否则会双重转义。

    Args:
        content: 原始内容字符串。

    Returns:
        转义后可安全嵌入规则字符串的内容。
    """
    return content.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def parse_rule(rule_string: str) -> PermissionRuleValue:
    """将规则字符串解析为 PermissionRuleValue。

    格式：
      "Bash"               → {tool_name: "Bash"}
      "Bash(npm install)"  → {tool_name: "Bash", rule_content: "npm install"}
      "Bash(*)" / "Bash()" → {tool_name: "Bash"}（通配符视为无内容限定）

    Args:
        rule_string: 规则字符串，来自配置文件或 CLI 参数。

    Returns:
        解析后的 PermissionRuleValue。
    """
    open_idx = _find_first_unescaped(rule_string, "(")
    if open_idx == -1:
        return PermissionRuleValue(tool_name=rule_string)

    close_idx = _find_last_unescaped(rule_string, ")")
    # 括号不匹配或闭括号不在末尾，降级为纯工具名
    if close_idx == -1 or close_idx <= open_idx or close_idx != len(rule_string) - 1:
        return PermissionRuleValue(tool_name=rule_string)

    tool_name = rule_string[:open_idx]
    if not tool_name:
        return PermissionRuleValue(tool_name=rule_string)

    raw_content = rule_string[open_idx + 1 : close_idx]

    # 空内容或纯通配符视为工具级规则，不带 content 限定
    if not raw_content or raw_content == "*":
        return PermissionRuleValue(tool_name=tool_name)

    return PermissionRuleValue(tool_name=tool_name, rule_content=_unescape_content(raw_content))


def rule_to_string(value: PermissionRuleValue) -> str:
    """将 PermissionRuleValue 序列化为规则字符串。

    Args:
        value: 待序列化的规则值。

    Returns:
        规则字符串，可安全存入配置文件。
    """
    if value.rule_content is None:
        return value.tool_name
    return f"{value.tool_name}({_escape_content(value.rule_content)})"
