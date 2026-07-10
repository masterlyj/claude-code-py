"""api/main.py 测试：覆盖 SSE 流、Ask 闭环、abort、错误路径。

测试策略：通过 monkeypatch 替换 `api.main.QueryEngine`，让 submit_message
产出可控事件序列，避免真调 Anthropic API。全程 async——用 httpx.AsyncClient
+ ASGITransport 驱动 FastAPI 应用，Ask 闭环用 asyncio.create_task 并发
执行"读 SSE 流"和"POST answer"，避免 TestClient 同步桥接层的线程死锁。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from api import main as api_main
from core.models import (
    MessageCompleteEvent,
    QueryCompleteEvent,
    StreamEvent,
    SubmitResultEvent,
    TextDeltaEvent,
)
from permissions.rules import AskDecision


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch: pytest.MonkeyPatch):
    """所有测试默认注入一个假 key，避开真实凭据校验。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key")


@pytest.fixture(autouse=True)
def _clear_shared_state():
    """每个测试用例前后清空模块级 pending_asks / _running，防止串扰。"""
    api_main._pending_asks.clear()
    api_main._running.clear()
    yield
    api_main._pending_asks.clear()
    api_main._running.clear()


async def _make_client() -> AsyncClient:
    """构造一个直连 ASGI 的 AsyncClient，不走真实 TCP。"""
    transport = ASGITransport(app=api_main.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _read_sse_events(resp) -> list[dict[str, Any]]:
    """把 SSE 响应解成 JSON 事件列表。"""
    events: list[dict[str, Any]] = []
    async for raw in resp.aiter_lines():
        if not raw:
            continue
        if raw.startswith("data: "):
            events.append(json.loads(raw[len("data: "):]))
    return events


def _install_fake_engine(
    monkeypatch: pytest.MonkeyPatch,
    scripted_events: list[StreamEvent],
    trigger_ask: bool = False,
) -> dict[str, Any]:
    """安装一个 fake QueryEngine，让 submit_message 依 script 产出。

    如果 trigger_ask=True，就在 script 中间调用一次 ask_user 回调，
    然后根据回调答案决定后续事件（用于 Ask 闭环测试）。

    Returns:
        一个 dict，包含 "ask_answer" 键，测试可读取它来断言回调收到的值。
    """
    holder: dict[str, Any] = {"ask_answer": None}

    class _FakeEngine:
        def __init__(self, config, tools, permission_manager, ask_user=None):
            self._config = config
            self._ask_user = ask_user
            self._messages: list[dict[str, Any]] = []

        def abort(self) -> None:
            pass

        async def submit_message(self, prompt: str) -> AsyncIterator[StreamEvent]:
            for event in scripted_events:
                yield event
                if trigger_ask and isinstance(event, TextDeltaEvent) and event.text == "__ASK__":
                    from tools.base import BaseTool

                    class _FakeTool(BaseTool):
                        name = "Bash"
                        description = "fake"
                        input_schema = {"type": "object"}

                        async def execute(self, tool_input, context):  # type: ignore[override]
                            yield "unused"

                    approved = await self._ask_user(
                        AskDecision(reason="fake ask reason"),
                        _FakeTool(),
                        {"command": "ls"},
                    )
                    holder["ask_answer"] = approved

    monkeypatch.setattr(api_main, "QueryEngine", _FakeEngine)
    return holder


# ---------------------------------------------------------------------------
# 基础：SSE 事件透传
# ---------------------------------------------------------------------------


async def test_chat_streams_events_and_finishes(monkeypatch: pytest.MonkeyPatch):
    """/chat 的 SSE 流应先推 stream_start，再透传 engine 的事件，
    最后随 submit_result 结束。"""
    scripted: list[StreamEvent] = [
        TextDeltaEvent(text="hello"),
        TextDeltaEvent(text=" world"),
        MessageCompleteEvent(stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 0}),
        QueryCompleteEvent(final_messages=[], total_turns=1, stopped_reason="end_turn"),
        SubmitResultEvent(subtype="success", num_turns=1, total_usage={"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 0}, permission_denials=[]),
    ]
    _install_fake_engine(monkeypatch, scripted)

    async with await _make_client() as client:
        async with client.stream("POST", "/chat", json={"prompt": "hi"}) as resp:
            assert resp.status_code == 200
            events = await _read_sse_events(resp)

    # 第一条应是 stream_start，含 run_id
    assert events[0]["type"] == "stream_start"
    assert "run_id" in events[0] and len(events[0]["run_id"]) > 0

    # 中间应看到两个 text_delta 拼成完整句子
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert "".join(e["text"] for e in text_events) == "hello world"

    # 最后一条应是 submit_result（QueryCompleteEvent 被 engine 层吞掉不透传）
    assert events[-1]["type"] == "submit_result"


async def test_chat_returns_500_when_api_key_missing(monkeypatch: pytest.MonkeyPatch):
    """未配置 ANTHROPIC_API_KEY 时 /chat 应 500，避免静默失败。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with await _make_client() as client:
        resp = await client.post("/chat", json={"prompt": "hi"})
    assert resp.status_code == 500
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


async def test_chat_returns_400_on_invalid_permission_mode(monkeypatch: pytest.MonkeyPatch):
    """无效的 permission_mode 字符串应被拒绝，避免落到 engine 层再报错。"""
    async with await _make_client() as client:
        resp = await client.post(
            "/chat",
            json={"prompt": "hi", "permission_mode": "not-a-real-mode"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Ask 闭环
# ---------------------------------------------------------------------------


async def _answer_when_pending(client: AsyncClient, approved: bool) -> None:
    """轮询等 pending_asks 出现，然后 POST 回答。用 asyncio.create_task 并发跑。"""
    for _ in range(200):  # 最多等 ~10 秒
        await asyncio.sleep(0.05)
        if api_main._pending_asks:
            ask_id = next(iter(api_main._pending_asks))
            r = await client.post(f"/ask/{ask_id}/answer", json={"approved": approved})
            assert r.status_code == 200
            return
    raise AssertionError("no pending ask appeared within timeout")


async def test_ask_closure_with_approval(monkeypatch: pytest.MonkeyPatch):
    """Ask 事件推给前端后，POST /ask/{id}/answer 允许，回调应返回 True。"""
    scripted: list[StreamEvent] = [
        TextDeltaEvent(text="__ASK__"),  # 触发 ask_user 的信号
        MessageCompleteEvent(stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}),
        QueryCompleteEvent(final_messages=[], total_turns=1, stopped_reason="end_turn"),
        SubmitResultEvent(subtype="success", num_turns=1, total_usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}, permission_denials=[]),
    ]
    holder = _install_fake_engine(monkeypatch, scripted, trigger_ask=True)

    async with await _make_client() as client:
        answer_task = asyncio.create_task(_answer_when_pending(client, approved=True))
        async with client.stream("POST", "/chat", json={"prompt": "hi"}) as resp:
            events = await _read_sse_events(resp)
        await answer_task

    assert holder["ask_answer"] is True
    ask_events = [e for e in events if e["type"] == "ask"]
    assert len(ask_events) == 1
    assert ask_events[0]["tool_name"] == "Bash"
    assert ask_events[0]["reason"] == "fake ask reason"


async def test_ask_closure_with_rejection(monkeypatch: pytest.MonkeyPatch):
    """Ask 拒绝时 ask_user 应返回 False。"""
    scripted: list[StreamEvent] = [
        TextDeltaEvent(text="__ASK__"),
        MessageCompleteEvent(stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}),
        QueryCompleteEvent(final_messages=[], total_turns=1, stopped_reason="end_turn"),
        SubmitResultEvent(subtype="success", num_turns=1, total_usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}, permission_denials=[]),
    ]
    holder = _install_fake_engine(monkeypatch, scripted, trigger_ask=True)

    async with await _make_client() as client:
        answer_task = asyncio.create_task(_answer_when_pending(client, approved=False))
        async with client.stream("POST", "/chat", json={"prompt": "hi"}) as resp:
            await _read_sse_events(resp)
        await answer_task

    assert holder["ask_answer"] is False


async def test_ask_answer_returns_404_for_unknown_id():
    """给一个不存在的 ask_id POST answer 时应 404，避免静默丢答案。"""
    async with await _make_client() as client:
        resp = await client.post("/ask/nonexistent/answer", json={"approved": True})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------


async def test_abort_returns_404_for_unknown_run_id():
    """abort 不存在的 run_id 应 404。"""
    async with await _make_client() as client:
        resp = await client.post("/chat/nonexistent/abort")
    assert resp.status_code == 404


async def _abort_when_pending(client: AsyncClient) -> None:
    """轮询等 pending ask + running 都出现后调 abort。"""
    for _ in range(200):
        await asyncio.sleep(0.05)
        if api_main._running and api_main._pending_asks:
            run_id = next(iter(api_main._running))
            r = await client.post(f"/chat/{run_id}/abort")
            assert r.status_code == 200
            return
    raise AssertionError("no running chat + pending ask appeared within timeout")


async def test_abort_releases_pending_asks_as_denied(monkeypatch: pytest.MonkeyPatch):
    """abort 时挂起的 Ask 应被自动释放为拒绝，避免 SSE 永远挂着。"""
    scripted: list[StreamEvent] = [
        TextDeltaEvent(text="__ASK__"),
        MessageCompleteEvent(stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}),
        QueryCompleteEvent(final_messages=[], total_turns=1, stopped_reason="end_turn"),
        SubmitResultEvent(subtype="success", num_turns=1, total_usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}, permission_denials=[]),
    ]
    holder = _install_fake_engine(monkeypatch, scripted, trigger_ask=True)

    async with await _make_client() as client:
        abort_task = asyncio.create_task(_abort_when_pending(client))
        async with client.stream("POST", "/chat", json={"prompt": "hi"}) as resp:
            await _read_sse_events(resp)
        await abort_task

    assert holder["ask_answer"] is False
