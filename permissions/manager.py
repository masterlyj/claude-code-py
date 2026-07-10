"""权限管理器，负责工具执行前的权限决策。

实现原版的六步决策流水线（顺序经对抗审查后调整）：
  1. deny 规则匹配 → 直接拒绝
  2. ask 规则匹配 → 需要用户确认
  3. bypass / plan 模式 → 直接允许（提前到工具自检之前，
     保持 "BYPASS regardless of rules" 的语义纯粹；对偶的
     TS 版 bypass-immune 白名单本项目暂不实现）
  4. 工具自身校验（tool.check_permissions），支持子命令级规则
  5. 剩余模式决策（accept_edits / dont_ask / auto）
  6. allow 规则匹配 → 直接允许
  7. passthrough → 转为 ask

原为"五步"，因引入工具自身细粒度校验步骤而扩展为六步。

对外暴露：
  PermissionContext — 规则集合与当前模式，不可变
  PermissionManager — 权限决策器
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from permissions.rules import (
    AllowDecision,
    AskDecision,
    DenyDecision,
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
    PermissionRuleValue,
    RuleSource,
    parse_rule,
)

if TYPE_CHECKING:
    from tools.base import BaseTool


# ── 权限上下文 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PermissionContext:
    """规则集合与当前权限模式，整个会话期间通常不变。

    frozen=True 使其不可变，避免多处意外修改导致状态不一致。
    修改规则时通过 with_rule() / without_rule() 创建新实例。

    Args:
        mode: 当前权限模式。
        allow_rules: 始终允许的规则列表。
        deny_rules: 始终拒绝的规则列表。
        ask_rules: 始终询问的规则列表。
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    allow_rules: tuple[PermissionRule, ...] = field(default_factory=tuple)
    deny_rules: tuple[PermissionRule, ...] = field(default_factory=tuple)
    ask_rules: tuple[PermissionRule, ...] = field(default_factory=tuple)

    @classmethod
    def from_rule_strings(
        cls,
        mode: PermissionMode = PermissionMode.DEFAULT,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        ask: list[str] | None = None,
        source: RuleSource = RuleSource.SESSION,
    ) -> PermissionContext:
        """从规则字符串列表构建 PermissionContext。

        便捷工厂方法，将 "Bash(git:*)" 这类字符串直接解析为规则对象。

        Args:
            mode: 权限模式。
            allow: allow 规则字符串列表。
            deny: deny 规则字符串列表。
            ask: ask 规则字符串列表。
            source: 规则来源，默认为 session（运行时动态添加）。

        Returns:
            构建好的 PermissionContext 实例。
        """
        def _parse(strings: list[str] | None, behavior: PermissionBehavior) -> tuple[PermissionRule, ...]:
            if not strings:
                return ()
            return tuple(
                PermissionRule(value=parse_rule(s), source=source, behavior=behavior)
                for s in strings
            )

        return cls(
            mode=mode,
            allow_rules=_parse(allow, PermissionBehavior.ALLOW),
            deny_rules=_parse(deny, PermissionBehavior.DENY),
            ask_rules=_parse(ask, PermissionBehavior.ASK),
        )

    def with_rule(self, rule: PermissionRule) -> PermissionContext:
        """返回追加了新规则的新上下文实例。

        Args:
            rule: 要追加的规则。

        Returns:
            包含新规则的 PermissionContext 副本。
        """
        match rule.behavior:
            case PermissionBehavior.ALLOW:
                return PermissionContext(
                    mode=self.mode,
                    allow_rules=self.allow_rules + (rule,),
                    deny_rules=self.deny_rules,
                    ask_rules=self.ask_rules,
                )
            case PermissionBehavior.DENY:
                return PermissionContext(
                    mode=self.mode,
                    allow_rules=self.allow_rules,
                    deny_rules=self.deny_rules + (rule,),
                    ask_rules=self.ask_rules,
                )
            case PermissionBehavior.ASK:
                return PermissionContext(
                    mode=self.mode,
                    allow_rules=self.allow_rules,
                    deny_rules=self.deny_rules,
                    ask_rules=self.ask_rules + (rule,),
                )


# ── 规则匹配 ──────────────────────────────────────────────────────────────


def _tool_matches_rule_value(tool_name: str, value: PermissionRuleValue) -> bool:
    """检查工具名是否与规则值匹配（仅工具级匹配，不含 content 限定）。

    只有 rule_content 为 None 的规则才参与工具级匹配，
    含 content 的规则（如 Bash(git:*)）交由工具自身的 check_permissions 处理。

    Args:
        tool_name: 工具名称。
        value: 规则值。

    Returns:
        True 表示匹配。
    """
    return value.rule_content is None and value.tool_name == tool_name


def _find_rule(
    rules: tuple[PermissionRule, ...],
    tool_name: str,
) -> PermissionRule | None:
    """在规则列表中查找第一条匹配指定工具的规则。

    Args:
        rules: 待搜索的规则元组。
        tool_name: 工具名称。

    Returns:
        第一条匹配的规则，未找到时为 None。
    """
    return next(
        (r for r in rules if _tool_matches_rule_value(tool_name, r.value)),
        None,
    )


# ── 权限管理器 ────────────────────────────────────────────────────────────


class PermissionManager:
    """工具执行前的权限决策器。

    流水线顺序（与原版一致，fail-closed 原则）：
      步骤 1 — deny 规则：直接拒绝
      步骤 2 — ask 规则：需要用户确认
      步骤 3 — bypass / plan 模式：直接允许（提前到工具自检前，保持
        BYPASS "regardless of rules" 语义纯粹）
      步骤 4 — 工具自身校验（tool.check_permissions），支持子命令级规则
      步骤 5 — 剩余模式决策（accept_edits / dont_ask / auto）
      步骤 6 — allow 规则：直接允许
      步骤 7 — passthrough → 转为 ask

    Args:
        context: 权限规则集合与模式配置。
    """

    def __init__(self, context: PermissionContext) -> None:
        self._context = context

    @property
    def context(self) -> PermissionContext:
        """当前权限上下文（只读）。"""
        return self._context

    def update_context(self, context: PermissionContext) -> None:
        """替换权限上下文，用于运行时动态修改规则或模式。

        Args:
            context: 新的权限上下文。
        """
        self._context = context

    async def check(
        self,
        tool: BaseTool,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """执行完整的六步权限决策流水线。

        Args:
            tool: 待执行的工具实例。
            tool_input: 工具调用参数。

        Returns:
            PermissionDecision 的某个子类型：AllowDecision、DenyDecision 或 AskDecision。
        """
        ctx = self._context
        tool_name = tool.name
        mode = ctx.mode

        # 步骤 1：deny 规则优先，fail-closed
        deny_rule = _find_rule(ctx.deny_rules, tool_name)
        if deny_rule is not None:
            return DenyDecision(reason=f"{tool_name} 被拒绝规则禁止使用")

        # 步骤 2：ask 规则
        ask_rule = _find_rule(ctx.ask_rules, tool_name)
        if ask_rule is not None:
            return AskDecision(
                reason=f"{tool_name} 的 ask 规则要求用户确认",
                rule=ask_rule,
            )

        # 步骤 3：bypass / plan 模式提前放行，保持 "regardless of rules" 语义
        # 且让工具自身校验只在受控模式下生效——避免出现"用户开了 BYPASS 但
        # 工具自检返回 Deny 导致执行不了"的语义冲突。TS 版的 bypass-immune
        # 白名单（.git/、.claude/ 等敏感路径即使 bypass 也要拦）本项目暂不
        # 实现，等 P0 闭环稳定后作为独立议题。
        if mode in (PermissionMode.BYPASS, PermissionMode.PLAN):
            return AllowDecision(reason=f"权限模式 {mode.value} 允许所有工具")

        # 步骤 4：工具自身校验（子命令级细粒度规则匹配）。返回：
        #   AllowDecision：工具明确放行，直接采纳
        #   DenyDecision：工具明确拒绝，直接采纳（此时 BYPASS 已在步骤 3 消耗）
        #   AskDecision：工具要求人工确认，稍后与 allow 规则一起裁决
        #   None：passthrough，交给流水线后续步骤
        tool_check = await tool.check_permissions(tool_input, ctx)
        if isinstance(tool_check, AllowDecision):
            return tool_check
        if isinstance(tool_check, DenyDecision):
            return tool_check
        # AskDecision 缓存至步骤 6 之后处理

        # 步骤 5：剩余模式决策
        if mode == PermissionMode.DONT_ASK:
            return DenyDecision(reason=f"当前权限模式 {mode.value} 拒绝所有需要确认的操作")

        if mode == PermissionMode.ACCEPT_EDITS and tool.is_read_only(tool_input):
            return AllowDecision(reason="accept_edits 模式允许只读操作")

        # 步骤 6：allow 规则
        allow_rule = _find_rule(ctx.allow_rules, tool_name)
        if allow_rule is not None:
            return AllowDecision(reason=f"allow 规则匹配：{tool_name}")

        # 步骤 7：工具校验返回了 ask，现在处理
        if isinstance(tool_check, AskDecision):
            return tool_check

        # auto 模式在二期由 ML 分类器决策，当前 fallback 到 ask
        if mode == PermissionMode.AUTO:
            return AskDecision(reason=f"auto 模式暂未实现分类器，需要用户确认 {tool_name}")

        # default / accept_edits（非只读操作）：需要用户确认
        return AskDecision(reason=f"需要用户确认是否允许 {tool_name} 执行")
