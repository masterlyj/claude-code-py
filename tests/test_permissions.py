"""Tests for the permissions subsystem.

Covers:
  - permissions/rules.py  — parse_rule, rule_to_string, decision types
  - permissions/manager.py — PermissionManager five-step decision pipeline
"""

from __future__ import annotations

import pytest

from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import (
    AllowDecision,
    AskDecision,
    DenyDecision,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleValue,
    RuleSource,
    parse_rule,
    rule_to_string,
)
from tools.file_read import FileReadTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    rule_string: str,
    behavior: PermissionBehavior = PermissionBehavior.ALLOW,
    source: RuleSource = RuleSource.SESSION,
) -> PermissionRule:
    """Build a PermissionRule from a raw string for use in test contexts."""
    return PermissionRule(
        value=parse_rule(rule_string),
        source=source,
        behavior=behavior,
    )


# ---------------------------------------------------------------------------
# parse_rule
# ---------------------------------------------------------------------------


def test_parse_rule_tool_only():
    """Parsing a bare tool name produces tool_name with no content."""
    result = parse_rule("Bash")

    assert result.tool_name == "Bash"
    assert result.rule_content is None


def test_parse_rule_with_content():
    """Parsing 'Tool(content)' extracts both tool_name and rule_content."""
    result = parse_rule("Bash(git status)")

    assert result.tool_name == "Bash"
    assert result.rule_content == "git status"


def test_parse_rule_wildcard_becomes_tool_only():
    """A lone wildcard inside parens is treated the same as no parens."""
    result = parse_rule("Bash(*)")

    assert result.tool_name == "Bash"
    assert result.rule_content is None


def test_parse_rule_empty_parens():
    """Empty parentheses are equivalent to omitting parens entirely."""
    result = parse_rule("Bash()")

    assert result.tool_name == "Bash"
    assert result.rule_content is None


def test_rule_to_string_roundtrip():
    """Serialising then re-parsing a rule yields the same value."""
    original = parse_rule("Read(src/main.py)")

    serialised = rule_to_string(original)
    reparsed = parse_rule(serialised)

    assert reparsed.tool_name == original.tool_name
    assert reparsed.rule_content == original.rule_content


def test_rule_to_string_roundtrip_tool_only():
    """Tool-only rules survive the serialise/parse roundtrip unchanged."""
    original = parse_rule("Write")

    serialised = rule_to_string(original)
    reparsed = parse_rule(serialised)

    assert reparsed.tool_name == "Write"
    assert reparsed.rule_content is None


def test_parse_rule_escaped_parens():
    """Content containing escaped parentheses is unescaped correctly."""
    # "Bash(python -c \"print\(1\)\")" ← escaped inner parens
    rule_str = r"Bash(python -c print\(1\))"
    result = parse_rule(rule_str)

    assert result.tool_name == "Bash"
    assert result.rule_content == "python -c print(1)"


# ---------------------------------------------------------------------------
# PermissionManager — pipeline decisions
# ---------------------------------------------------------------------------


async def test_manager_bypass_allows_all():
    """BYPASS mode returns AllowDecision for any tool regardless of rules."""
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, AllowDecision)


async def test_manager_deny_rule_blocks():
    """A deny rule for a tool causes DenyDecision before any other check."""
    ctx = PermissionContext.from_rule_strings(
        mode=PermissionMode.DEFAULT,
        deny=["Read"],
    )
    manager = PermissionManager(ctx)
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, DenyDecision)


async def test_manager_allow_rule_passes():
    """An allow rule for a tool causes AllowDecision in DEFAULT mode."""
    ctx = PermissionContext.from_rule_strings(
        mode=PermissionMode.DEFAULT,
        allow=["Read"],
    )
    manager = PermissionManager(ctx)
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, AllowDecision)


async def test_manager_default_no_rules_returns_ask():
    """DEFAULT mode with no matching rules falls through to AskDecision."""
    manager = PermissionManager(PermissionContext(mode=PermissionMode.DEFAULT))
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, AskDecision)


async def test_manager_dont_ask_converts_to_deny():
    """DONT_ASK mode converts any would-be AskDecision into DenyDecision."""
    manager = PermissionManager(PermissionContext(mode=PermissionMode.DONT_ASK))
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, DenyDecision)


async def test_manager_deny_takes_precedence_over_allow():
    """A deny rule beats an allow rule for the same tool (fail-closed)."""
    ctx = PermissionContext.from_rule_strings(
        mode=PermissionMode.DEFAULT,
        allow=["Read"],
        deny=["Read"],
    )
    manager = PermissionManager(ctx)
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, DenyDecision)


async def test_manager_ask_rule_returns_ask_in_default_mode():
    """An explicit ask rule triggers AskDecision and carries the rule reference."""
    ctx = PermissionContext.from_rule_strings(
        mode=PermissionMode.DEFAULT,
        ask=["Read"],
    )
    manager = PermissionManager(ctx)
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, AskDecision)
    assert decision.rule is not None
    assert decision.rule.value.tool_name == "Read"


async def test_manager_update_context_is_reflected_immediately():
    """Replacing the context via update_context affects subsequent checks."""
    manager = PermissionManager(PermissionContext(mode=PermissionMode.DEFAULT))
    tool = FileReadTool()

    # First check in default mode → ask
    first = await manager.check(tool, {"file_path": "/tmp/x.txt"})
    assert isinstance(first, AskDecision)

    # Replace context with BYPASS
    manager.update_context(PermissionContext(mode=PermissionMode.BYPASS))

    second = await manager.check(tool, {"file_path": "/tmp/x.txt"})
    assert isinstance(second, AllowDecision)


async def test_manager_plan_mode_allows_all():
    """PLAN mode behaves like BYPASS and returns AllowDecision."""
    manager = PermissionManager(PermissionContext(mode=PermissionMode.PLAN))
    tool = FileReadTool()

    decision = await manager.check(tool, {"file_path": "/tmp/x.txt"})

    assert isinstance(decision, AllowDecision)
