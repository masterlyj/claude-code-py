"""Tests for QueryEngine, the session orchestration layer over query().

QueryEngine wraps the atomic query() loop into a multi-turn session that
maintains message history, accumulates token usage, tracks permission
denials, and responds to abort signals.

These tests replace `core.engine.query` with a fake async generator so the
suite runs offline. What we verify is the engine's state-management logic:
that it correctly threads messages across turns, aggregates usage, records
denials per-submit, and routes each event type. The underlying query()
loop has its own coverage via the tool / permission tests.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

import pytest

from core.engine import QueryEngine
from core.models import (
    EngineConfig,
    MessageCompleteEvent,
    QueryCompleteEvent,
    QueryParams,
    StreamEvent,
    SubmitResultEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import DenyDecision, PermissionMode


# ---------------------------------------------------------------------------
# Test helpers — fake query() that lets each test script its own event stream
# ---------------------------------------------------------------------------


def make_fake_query(
    script: Callable[[QueryParams], list[StreamEvent]],
) -> Callable[..., AsyncIterator[StreamEvent]]:
    """Build a fake query() from a synchronous script function.

    The script receives the QueryParams the engine assembled and returns
    the exact list of events to yield in order. Tests use this to control
    exactly what the engine sees without hitting a real API.
    """

    async def fake_query(
        params: QueryParams,
        tools: Any,
        permission_manager: Any,
        abort_event: Any = None,
        ask_user: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        for event in script(params):
            yield event

    return fake_query


def build_engine(system_prompt: str = "system") -> QueryEngine:
    """Build a QueryEngine with bypass-mode permissions and no tools."""
    config = EngineConfig(
        system_prompt=system_prompt,
        model="claude-test",
        api_key="fake-key",
    )
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))
    return QueryEngine(config, tools=[], permission_manager=manager)


async def collect_events(gen: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    """Drain an async event stream into a list for easy assertions."""
    return [event async for event in gen]


# ---------------------------------------------------------------------------
# Message history accumulation
# ---------------------------------------------------------------------------


async def test_submit_message_appends_user_and_absorbs_final_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After submit_message returns, engine.messages must contain the
    final list from QueryCompleteEvent (which query() derives from the
    initial user message + assistant reply)."""

    def script(params: QueryParams) -> list[StreamEvent]:
        # Sanity: the user message the engine appended is visible to query()
        assert params.messages[-1] == {"role": "user", "content": "hello"}
        # Simulate a normal end_turn: text + message_complete + query_complete
        return [
            TextDeltaEvent(text="hi"),
            MessageCompleteEvent(stop_reason="end_turn", usage={
                "input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 0,
            }),
            QueryCompleteEvent(
                final_messages=params.messages + [
                    {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
                ],
                total_turns=1,
                stopped_reason="end_turn",
            ),
        ]

    monkeypatch.setattr("core.engine.query", make_fake_query(script))
    engine = build_engine()

    events = await collect_events(engine.submit_message("hello"))

    # 5 events: user push + query stream (2 events) + submit_result
    # Actually: text_delta, message_complete, submit_result (QueryComplete not透传)
    assert [type(e).__name__ for e in events] == [
        "TextDeltaEvent",
        "MessageCompleteEvent",
        "SubmitResultEvent",
    ]
    assert engine.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]


async def test_multi_turn_uses_accumulated_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second submit_message must see the assistant reply from the first
    turn in QueryParams.messages."""
    call_log: list[list[dict[str, Any]]] = []

    def script(params: QueryParams) -> list[StreamEvent]:
        # Snapshot exactly what messages query() received this call
        call_log.append(list(params.messages))
        assistant_text = f"reply-{len(call_log)}"
        return [
            MessageCompleteEvent(stop_reason="end_turn", usage={
                "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0,
            }),
            QueryCompleteEvent(
                final_messages=params.messages + [
                    {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]},
                ],
                total_turns=1,
                stopped_reason="end_turn",
            ),
        ]

    monkeypatch.setattr("core.engine.query", make_fake_query(script))
    engine = build_engine()

    await collect_events(engine.submit_message("first"))
    await collect_events(engine.submit_message("second"))

    # First call sees only the first user prompt
    assert call_log[0] == [{"role": "user", "content": "first"}]
    # Second call sees: user1 + assistant1 + user2
    assert call_log[1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "reply-1"}]},
        {"role": "user", "content": "second"},
    ]


# ---------------------------------------------------------------------------
# Usage accumulation
# ---------------------------------------------------------------------------


async def test_total_usage_accumulates_across_message_complete_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each MessageCompleteEvent's usage should add into engine.total_usage."""

    def script(params: QueryParams) -> list[StreamEvent]:
        return [
            MessageCompleteEvent(stop_reason="end_turn", usage={
                "input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5,
            }),
            MessageCompleteEvent(stop_reason="end_turn", usage={
                "input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 2,
            }),
            QueryCompleteEvent(
                final_messages=params.messages,
                total_turns=2,
                stopped_reason="end_turn",
            ),
        ]

    monkeypatch.setattr("core.engine.query", make_fake_query(script))
    engine = build_engine()

    await collect_events(engine.submit_message("q"))

    assert engine.total_usage == {
        "input_tokens": 13,
        "output_tokens": 27,
        "cache_read_input_tokens": 7,
    }


# ---------------------------------------------------------------------------
# Stopped reason routing
# ---------------------------------------------------------------------------


async def test_max_turns_stopped_reason_maps_to_submit_result_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QueryCompleteEvent(stopped_reason='max_turns') must produce
    SubmitResultEvent(subtype='max_turns')."""

    def script(params: QueryParams) -> list[StreamEvent]:
        return [QueryCompleteEvent(
            final_messages=params.messages,
            total_turns=3,
            stopped_reason="max_turns",
        )]

    monkeypatch.setattr("core.engine.query", make_fake_query(script))
    engine = build_engine()

    events = await collect_events(engine.submit_message("q"))

    result = events[-1]
    assert isinstance(result, SubmitResultEvent)
    assert result.subtype == "max_turns"
    assert result.num_turns == 3


async def test_aborted_stopped_reason_maps_to_submit_result_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QueryCompleteEvent(stopped_reason='aborted') must produce
    SubmitResultEvent(subtype='aborted')."""

    def script(params: QueryParams) -> list[StreamEvent]:
        return [QueryCompleteEvent(
            final_messages=params.messages,
            total_turns=1,
            stopped_reason="aborted",
        )]

    monkeypatch.setattr("core.engine.query", make_fake_query(script))
    engine = build_engine()

    events = await collect_events(engine.submit_message("q"))

    result = events[-1]
    assert isinstance(result, SubmitResultEvent)
    assert result.subtype == "aborted"


# ---------------------------------------------------------------------------
# Abort signal propagation
# ---------------------------------------------------------------------------


async def test_abort_sets_the_shared_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """engine.abort() must set the abort_event passed into query()."""
    captured_abort_event: list[Any] = []

    async def fake_query(
        params: QueryParams,
        tools: Any,
        permission_manager: Any,
        abort_event: Any = None,
        ask_user: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        captured_abort_event.append(abort_event)
        yield QueryCompleteEvent(
            final_messages=params.messages,
            total_turns=0,
            stopped_reason="end_turn",
        )

    monkeypatch.setattr("core.engine.query", fake_query)
    engine = build_engine()

    # Kick off submit_message, then abort mid-flight
    gen = engine.submit_message("q")
    # Prime the generator so query() runs and captures abort_event
    async for _ in gen:
        pass

    # The captured event should be the same one that abort() would set
    assert captured_abort_event
    ev = captured_abort_event[0]
    assert not ev.is_set()
    engine.abort()
    assert ev.is_set()


# ---------------------------------------------------------------------------
# Permission denial tracking
# ---------------------------------------------------------------------------


class _AlwaysDenyManager(PermissionManager):
    """A manager that denies every check() call. Used to feed denials into
    the engine's decorator without needing a real tool."""

    def __init__(self) -> None:
        super().__init__(PermissionContext(mode=PermissionMode.DEFAULT))

    async def check(self, tool: Any, tool_input: dict[str, Any]) -> Any:  # noqa: D401
        return DenyDecision(reason="denied for test")


async def test_permission_denials_recorded_when_wrapped_manager_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the wrapped permission manager denies a call, engine records it."""

    async def fake_query(
        params: QueryParams,
        tools: Any,
        permission_manager: Any,
        abort_event: Any = None,
        ask_user: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        # Simulate query() invoking permission checks like _execute_tool does
        # We use a stub tool object with just a .name attribute
        class _StubTool:
            name = "Bash"

        await permission_manager.check(_StubTool(), {"command": "rm -rf /"})
        yield QueryCompleteEvent(
            final_messages=params.messages,
            total_turns=1,
            stopped_reason="end_turn",
        )

    monkeypatch.setattr("core.engine.query", fake_query)

    config = EngineConfig(system_prompt="s", api_key="k")
    engine = QueryEngine(config, tools=[], permission_manager=_AlwaysDenyManager())

    events = await collect_events(engine.submit_message("q"))

    assert len(engine.permission_denials) == 1
    denial = engine.permission_denials[0]
    assert denial["tool_name"] == "Bash"
    assert denial["tool_input"] == {"command": "rm -rf /"}
    assert denial["behavior"] == "deny"

    # SubmitResultEvent should include this turn's denial
    result = events[-1]
    assert isinstance(result, SubmitResultEvent)
    assert len(result.permission_denials) == 1


async def test_submit_result_denials_are_window_scoped_not_cumulative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SubmitResultEvent.permission_denials must only include denials from
    the current submit_message, even though engine.permission_denials accumulates."""

    async def fake_query(
        params: QueryParams,
        tools: Any,
        permission_manager: Any,
        abort_event: Any = None,
        ask_user: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        class _StubTool:
            name = "Bash"

        await permission_manager.check(_StubTool(), {"cmd": "x"})
        yield QueryCompleteEvent(
            final_messages=params.messages,
            total_turns=1,
            stopped_reason="end_turn",
        )

    monkeypatch.setattr("core.engine.query", fake_query)

    config = EngineConfig(system_prompt="s", api_key="k")
    engine = QueryEngine(config, tools=[], permission_manager=_AlwaysDenyManager())

    events1 = await collect_events(engine.submit_message("q1"))
    events2 = await collect_events(engine.submit_message("q2"))

    # Engine tracks cumulative denials
    assert len(engine.permission_denials) == 2

    # But each SubmitResultEvent only sees its own turn's denials
    for events in (events1, events2):
        result = events[-1]
        assert isinstance(result, SubmitResultEvent)
        assert len(result.permission_denials) == 1


# ---------------------------------------------------------------------------
# Event passthrough
# ---------------------------------------------------------------------------


async def test_tool_use_and_result_events_are_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine transparently forwards ToolUseEvent / ToolResultEvent to the
    caller; QueryCompleteEvent is the only event NOT forwarded (internal only)."""

    def script(params: QueryParams) -> list[StreamEvent]:
        return [
            ToolUseEvent(tool_use_id="t1", tool_name="Bash", tool_input={"cmd": "ls"}),
            ToolResultEvent(tool_use_id="t1", tool_name="Bash", content="output", is_error=False),
            QueryCompleteEvent(
                final_messages=params.messages,
                total_turns=1,
                stopped_reason="end_turn",
            ),
        ]

    monkeypatch.setattr("core.engine.query", make_fake_query(script))
    engine = build_engine()

    events = await collect_events(engine.submit_message("q"))
    types = [type(e).__name__ for e in events]

    assert "ToolUseEvent" in types
    assert "ToolResultEvent" in types
    assert "QueryCompleteEvent" not in types  # internal only, absorbed by engine
    assert types[-1] == "SubmitResultEvent"
