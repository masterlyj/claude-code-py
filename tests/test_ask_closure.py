"""Ask 闭环测试：验证 ask_user 回调能真的让 Ask 决策转化为执行或拒绝。

前置：AskDecision 从 P0 前的"纯装饰性代码"变成能被 query() 消费的可闭环
状态——ask_user 返回 True 时工具会被执行，返回 False / 未提供时会返回
拒绝文本给模型。

这些测试直接调 _execute_tool 而非跑完整 query 循环，专注验证 Ask 分支
的语义，跟 API 调用解耦。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.query import _execute_tool
from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import (
    AllowDecision,
    AskDecision,
    DenyDecision,
    PermissionDecision,
    PermissionMode,
)
from tools.base import BaseTool
from tools.file_read import FileReadTool


class _StubAlwaysAskManager(PermissionManager):
    """强制返回 AskDecision 的权限管理器，剥离底层规则匹配噪音。"""

    def __init__(self) -> None:
        super().__init__(PermissionContext(mode=PermissionMode.DEFAULT))

    async def check(self, tool: BaseTool, tool_input: dict[str, Any]) -> PermissionDecision:
        return AskDecision(reason="test always asks")


async def test_ask_without_callback_is_treated_as_deny(tmp_path):
    """未提供 ask_user 回调时，Ask 保守视为拒绝——避免旧调用方在不知不觉中
    因权限系统的新语义把敏感操作放行。"""
    target = tmp_path / "hi.txt"
    target.write_text("hi\n", encoding="utf-8")

    tool_use = {
        "id": "t1",
        "name": "Read",
        "input": {"file_path": str(target)},
    }
    result, is_error = await _execute_tool(
        tool_use,
        tools=[FileReadTool()],
        permission_manager=_StubAlwaysAskManager(),
        ask_user=None,  # 显式不提供
    )
    assert is_error is True
    assert "未提供回调" in result or "拒绝" in result


async def test_ask_with_approving_callback_runs_the_tool(tmp_path):
    """ask_user 返回 True 时工具真的被执行，Ask 从"装饰"变成"闭环"。"""
    target = tmp_path / "hi.txt"
    target.write_text("hi\n", encoding="utf-8")

    calls: list[tuple[str, dict]] = []

    async def approving_asker(decision, tool, tool_input):
        calls.append((tool.name, tool_input))
        return True

    tool_use = {
        "id": "t2",
        "name": "Read",
        "input": {"file_path": str(target)},
    }
    result, is_error = await _execute_tool(
        tool_use,
        tools=[FileReadTool()],
        permission_manager=_StubAlwaysAskManager(),
        ask_user=approving_asker,
    )

    assert is_error is False
    # FileReadTool 会输出带行号的内容
    assert "hi" in result
    assert calls == [("Read", {"file_path": str(target)})]


async def test_ask_with_rejecting_callback_denies_the_tool(tmp_path):
    """ask_user 返回 False 时工具不被执行，返回拒绝文本给模型。"""
    target = tmp_path / "hi.txt"
    target.write_text("secret\n", encoding="utf-8")

    async def rejecting_asker(decision, tool, tool_input):
        return False

    tool_use = {
        "id": "t3",
        "name": "Read",
        "input": {"file_path": str(target)},
    }
    result, is_error = await _execute_tool(
        tool_use,
        tools=[FileReadTool()],
        permission_manager=_StubAlwaysAskManager(),
        ask_user=rejecting_asker,
    )

    assert is_error is True
    assert "拒绝" in result
    # 关键：文件内容不应被读出来
    assert "secret" not in result


async def test_deny_bypasses_ask_user(tmp_path):
    """DenyDecision 无论是否提供 ask_user 都直接拒绝，不会调用回调。"""
    target = tmp_path / "hi.txt"
    target.write_text("hi\n", encoding="utf-8")

    ask_called = False

    async def asker(decision, tool, tool_input):
        nonlocal ask_called
        ask_called = True
        return True

    ctx = PermissionContext.from_rule_strings(
        mode=PermissionMode.DEFAULT,
        deny=["Read"],
    )
    manager = PermissionManager(ctx)
    tool_use = {
        "id": "t4",
        "name": "Read",
        "input": {"file_path": str(target)},
    }
    result, is_error = await _execute_tool(
        tool_use,
        tools=[FileReadTool()],
        permission_manager=manager,
        ask_user=asker,
    )

    assert is_error is True
    assert "拒绝" in result
    assert ask_called is False, "Deny 分支不应调用 ask_user"


# ---------------------------------------------------------------------------
# FileReadTool.is_read_only —— 之前是死码的 P0.5 修复
# ---------------------------------------------------------------------------


def test_file_read_declares_read_only():
    """FileReadTool 现在明确声明只读，让 accept_edits 模式能真正优化只读操作。"""
    assert FileReadTool().is_read_only({"file_path": "/tmp/x"}) is True


async def test_accept_edits_allows_read_operation():
    """accept_edits 模式下只读工具直接放行，无需额外规则。"""
    ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    manager = PermissionManager(ctx)
    decision = await manager.check(FileReadTool(), {"file_path": "/tmp/x"})
    assert isinstance(decision, AllowDecision)
