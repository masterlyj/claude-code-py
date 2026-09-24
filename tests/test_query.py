"""query() 循环的直接回归测试。

engine / api 层测试通过 mock 隔离 query() 循环内部行为，导致 query.py
本体的一些边界路径（比如 max_turns 提前退出时 emit synthetic tool_result）
从未被直接验证。本文件补齐这类回归防线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

from core.models import (
    QueryCompleteEvent,
    QueryParams,
    ToolResultEvent,
    ToolUseEvent,
)
from core.query import query
from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import PermissionMode
from tools.base import BaseTool, ToolUseContext, ValidationResult


# ---------------------------------------------------------------------------
# 桩：一个总是"发起 tool_use"的假 anthropic 流
# ---------------------------------------------------------------------------


@dataclass
class _FakeContentBlock:
    type: str
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 20
    cache_read_input_tokens: int = 0


@dataclass
class _FakeFinalMessage:
    content: list[_FakeContentBlock]
    usage: _FakeUsage = field(default_factory=_FakeUsage)


class _FakeStreamCtx:
    """模仿 anthropic AsyncMessageStreamManager。

    每次进入 async with 时先 yield 一个空的 event stream（表示无 text_delta），
    然后 message_stop → get_final_message 返回一个只含 tool_use 的 assistant
    消息，让 query() 循环认为模型请求了工具。

    stop_reason 可参数化：默认 "tool_use"（正常工具轮次），传 "max_tokens"
    可模拟"响应被截断但 tool_use block 已完整流出"的场景。
    """

    def __init__(self, tool_use_id: str, tool_name: str, stop_reason: str):
        self._tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._stop_reason = stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self) -> AsyncIterator[Any]:
        # 只 yield 一个 message_delta（携带 stop_reason）+ message_stop
        yield _FakeEvent(
            type="message_delta", delta=_FakeDelta(stop_reason=self._stop_reason)
        )
        yield _FakeEvent(type="message_stop")

    async def get_final_message(self) -> _FakeFinalMessage:
        return _FakeFinalMessage(
            content=[
                _FakeContentBlock(
                    type="tool_use",
                    id=self._tool_use_id,
                    name=self._tool_name,
                    input={"command": "echo hi"},
                )
            ]
        )


@dataclass
class _FakeDelta:
    stop_reason: str | None = None
    type: str = "text_delta"
    text: str = ""


@dataclass
class _FakeEvent:
    type: str
    delta: _FakeDelta = field(default_factory=_FakeDelta)


class _FakeMessages:
    def __init__(self, tool_use_id: str, tool_name: str, stop_reason: str):
        self._id = tool_use_id
        self._name = tool_name
        self._stop_reason = stop_reason

    def stream(self, **_kwargs):
        return _FakeStreamCtx(self._id, self._name, self._stop_reason)


class _FakeAnthropic:
    def __init__(self, tool_use_id: str, tool_name: str, stop_reason: str = "tool_use"):
        self.messages = _FakeMessages(tool_use_id, tool_name, stop_reason)


# ---------------------------------------------------------------------------
# 桩：一个无副作用的假工具
# ---------------------------------------------------------------------------


class _EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "Echo"

    @property
    def description(self) -> str:
        return "test echo tool"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"command": {"type": "string"}}}

    async def validate_input(self, tool_input: dict[str, Any]) -> ValidationResult:
        return ValidationResult.passed()

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        yield "echoed"


# ---------------------------------------------------------------------------
# 断言辅助：消息序列是否满足 Anthropic 的 tool_use / tool_result 配对约束
# ---------------------------------------------------------------------------


def _assert_last_assistant_paired(
    msgs: list[dict[str, Any]], tool_use_id: str
) -> None:
    """断言最后一条 assistant 里的每个 tool_use 都被下一条 user 消息配对。"""
    last_assistant_idx = max(
        i for i, m in enumerate(msgs) if m.get("role") == "assistant"
    )
    assistant_msg = msgs[last_assistant_idx]
    tool_use_ids_in_assistant = [
        b["id"] for b in assistant_msg["content"] if b.get("type") == "tool_use"
    ]
    assert tool_use_id in tool_use_ids_in_assistant

    # 紧跟的下一条必须是 user 消息且含所有 tool_use_id 对应的 tool_result
    next_msg = msgs[last_assistant_idx + 1]
    assert next_msg["role"] == "user"
    result_ids = {
        b["tool_use_id"]
        for b in next_msg["content"]
        if b.get("type") == "tool_result"
    }
    assert result_ids == set(tool_use_ids_in_assistant)


# ---------------------------------------------------------------------------
# 测试：循环提前退出时必须为 pending tool_use 补 tool_result
# ---------------------------------------------------------------------------


async def test_max_turns_emits_synthetic_tool_result_for_pending_tool_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_turns 触发时，assistant 消息里已经有 tool_use blocks。如果不
    合成对应 tool_result，state.messages 就形成 Anthropic API 拒绝的
    "orphan tool_use" 序列，下次调用方重放时会 400。"""
    tool_use_id = "toolu_test_orphan"
    monkeypatch.setattr(
        "core.query.anthropic.AsyncAnthropic",
        lambda **_: _FakeAnthropic(tool_use_id, "Echo"),
    )

    params = QueryParams(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="s",
        model="fake",
        api_key="fake",
        max_turns=1,  # 关键：第 1 轮结束就命中上限
    )
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))
    tools = [_EchoTool()]

    events: list[Any] = []
    async for event in query(params, tools, manager):
        events.append(event)

    # 断言 1：应该发出了 tool_result 事件，即使工具从未真正执行过
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_use_id == tool_use_id
    assert tool_results[0].is_error is True

    # 断言 2：QueryCompleteEvent.final_messages 形成 API 认可的合法序列
    complete = next(e for e in events if isinstance(e, QueryCompleteEvent))
    assert complete.stopped_reason == "max_turns"
    _assert_last_assistant_paired(complete.final_messages, tool_use_id)


async def test_truncated_response_emits_synthetic_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_reason 不是 tool_use 却带回 tool_use block（max_tokens 截断）时，
    循环走的是"模型不再请求工具"的 break。这条路径同样把含 tool_use 的
    assistant 消息留在历史里，同样必须补 tool_result——截断比 max_turns
    更隐蔽：调用方只看 stop_reason 不会意识到历史里有孤儿 tool_use。"""
    tool_use_id = "toolu_test_truncated"
    monkeypatch.setattr(
        "core.query.anthropic.AsyncAnthropic",
        lambda **_: _FakeAnthropic(tool_use_id, "Echo", stop_reason="max_tokens"),
    )

    params = QueryParams(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="s",
        model="fake",
        api_key="fake",
        # 不设 max_turns：确保走的是 stop_reason 那条 break，而不是轮次上限
    )
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))

    events: list[Any] = []
    async for event in query(params, [_EchoTool()], manager):
        events.append(event)

    # 工具没有执行，但必须留下"因截断未执行"的 tool_result
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_use_id == tool_use_id
    assert tool_results[0].is_error is True

    complete = next(e for e in events if isinstance(e, QueryCompleteEvent))
    _assert_last_assistant_paired(complete.final_messages, tool_use_id)


async def test_tool_use_events_emitted_before_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """辅助断言：合成 tool_result 之前，query() 应该已经把 ToolUseEvent
    推给了前端——否则 UI 会看到"凭空冒出的 tool_result"。"""
    monkeypatch.setattr(
        "core.query.anthropic.AsyncAnthropic",
        lambda **_: _FakeAnthropic("t1", "Echo"),
    )

    params = QueryParams(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="s",
        model="fake",
        api_key="fake",
        max_turns=1,
    )
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))
    events: list[Any] = []
    async for event in query(params, [_EchoTool()], manager):
        events.append(event)

    # tool_use 事件必须早于 tool_result 事件
    tool_use_idx = next(
        i for i, e in enumerate(events) if isinstance(e, ToolUseEvent)
    )
    tool_result_idx = next(
        i for i, e in enumerate(events) if isinstance(e, ToolResultEvent)
    )
    assert tool_use_idx < tool_result_idx
