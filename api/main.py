"""FastAPI 入口：把 QueryEngine 通过 SSE 暴露给浏览器前端，并协调 Ask 决策的
人机回环。

三个端点：
  POST /chat          — 发送用户消息，返回 SSE 事件流（StreamEvent 系列）
  POST /ask/{tool_use_id}/answer — 前端对 Ask 事件的响应（allow / deny）
  POST /chat/{run_id}/abort      — 中止某次运行

Ask 协调的关键设计：
  query() 里的 ask_user 是 async 回调。当权限系统判为 Ask 时，回调把 tool_use_id
  作为 key 塞进模块级 pending_asks，然后 await 一个 asyncio.Event 直到超时或
  前端 POST 回答。用 tool_use_id 而不是另造 ask_id：它已经是 API 层保证唯一的
  标识，前端在 ToolUseEvent 里已经看到过这个 id，用它做协调 key 不需要额外通信。

  这个"HTTP 端点通过内存 dict 唤醒等待中的 async 回调"模式，是 human-in-the-loop
  跨 HTTP 无状态边界的最小可行实现，也是这个学习项目里第一次让 permissions 三
  态判断从测试走进真实产品链路。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.engine import QueryEngine
from core.models import EngineConfig, StreamEvent, SubmitResultEvent
from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import AskDecision, PermissionMode
from tools.base import BaseTool
from tools.registry import get_tools


# ── 全局状态：Ask 协调 + 运行中的 QueryEngine ──────────────────────────────


@dataclass
class _PendingAsk:
    """等待前端响应的一次 Ask 请求。"""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    answer: bool = False  # True 表示允许，False 表示拒绝


@dataclass
class _RunningEngine:
    """一次运行期间的 QueryEngine 句柄，供 /chat/{run_id}/abort 使用。"""

    engine: QueryEngine
    pending_ask_ids: set[str] = field(default_factory=set)


# tool_use_id → PendingAsk 的等待映射；SSE 生成器和 /answer 端点通过它交换答案
_pending_asks: dict[str, _PendingAsk] = {}

# run_id → RunningEngine；/abort 端点通过它调用 engine.abort() 并唤醒挂起的 Ask
_running: dict[str, _RunningEngine] = {}

# 单次 Ask 的最长等待时间。超时后兜底拒绝，避免连接挂断时 QueryEngine 无限期
# 挂起。前端保持页面开着就不会碰这个上限。
_ASK_TIMEOUT_SECONDS = 15 * 60


# ── 请求体模型 ────────────────────────────────────────────────────────────


class _RuleSet(BaseModel):
    """从前端接收的规则字符串集合。"""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """POST /chat 的请求体。

    Attributes:
        prompt: 本次要提交的用户输入文本。
        messages: 之前累计的消息历史（前端 localStorage 里持久化的）。
            后端 stateless，每次带全量历史过来。
        system_prompt: 系统提示。默认 "You are Claude..." 由前端决定。
        model: 模型 ID。
        max_turns: 单次工具调用最大轮次。
        permission_mode: 权限模式，字符串对齐 PermissionMode 枚举值。
        rules: 规则字符串集合。
    """

    prompt: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str = "You are a concise coding assistant."
    model: str = "claude-sonnet-4-6"
    max_turns: int = 10
    permission_mode: str = PermissionMode.DEFAULT.value
    rules: _RuleSet = Field(default_factory=_RuleSet)


class AskAnswer(BaseModel):
    """POST /ask/{tool_use_id}/answer 的请求体。"""

    approved: bool


# ── 应用装配 ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动钩子：加载 .env 到进程环境，覆盖已存在的同名变量。

    override=True 让项目 .env 作为权威配置源：ANTHROPIC_API_KEY /
    ANTHROPIC_BASE_URL 等应以项目配置为准，而不是被外层 shell 环境覆盖。
    """
    load_dotenv(override=True)
    yield


app = FastAPI(title="claude-code-py", lifespan=_lifespan)

# 前端开发时跑在 :5173，用宽松 CORS 便于本地开发；生产部署应收紧到具体域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── SSE 序列化辅助 ────────────────────────────────────────────────────────


def _sse_pack(event: StreamEvent | BaseModel) -> str:
    """把一个 pydantic 事件对象打包成 SSE data 帧。

    只带 data 字段（不用 event: 命名事件），因为事件类型已经在 JSON payload
    的 type 字段里，前端按 type 分发即可，避免两套并行的 tag 机制。
    """
    return f"data: {event.model_dump_json()}\n\n"


class _AskEvent(BaseModel):
    """SSE 里"需要用户确认"这个额外事件——core.models 里没有对应类型，
    因为 permissions 层的 Ask 是 python 内部概念，只在 API 层浮现给前端。

    ask_id 是本次 Ask 协调的唯一 key，前端 POST /ask/{ask_id}/answer 时
    要原样带回来。它跟模型层的 tool_use_id 无关（Ask 回调签名里根本拿不到
    tool_use_id），是 API 层新造的。
    """

    type: str = "ask"
    ask_id: str
    tool_name: str
    tool_input: dict[str, Any]
    reason: str


class _StreamStartEvent(BaseModel):
    """SSE 建立后立刻推给前端的第一条事件，携带本次运行 id 供后续 abort/answer。"""

    type: str = "stream_start"
    run_id: str


class _ErrorEvent(BaseModel):
    """内部异常统一走这条事件，前端展示为错误 toast，不刷新页面。"""

    type: str = "error"
    message: str


# ── /chat 主端点 ──────────────────────────────────────────────────────────


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """执行一次 QueryEngine.submit_message()，通过 SSE 推所有事件。

    响应形态：Server-Sent Events。每条事件是一行 `data: <json>\\n\\n`。
    JSON 的 type 字段用于前端分发，包含所有 core.models.StreamEvent 子类型，
    加上 API 层扩展的 stream_start / ask / error。
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set")

    try:
        mode = PermissionMode(req.permission_mode)
    except ValueError as exc:
        raise HTTPException(400, f"invalid permission_mode: {exc}") from None

    permission_ctx = PermissionContext.from_rule_strings(
        mode=mode,
        allow=req.rules.allow,
        deny=req.rules.deny,
        ask=req.rules.ask,
    )
    permission_mgr = PermissionManager(permission_ctx)

    tools: list[BaseTool] = list(get_tools())

    config = EngineConfig(
        system_prompt=req.system_prompt,
        model=req.model,
        api_key=api_key,
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
        max_turns_per_submit=req.max_turns,
    )

    run_id = uuid.uuid4().hex
    # closure 里用到的可变集合：_ask_user 往里加 ask_id，release_pending_asks 遍历它。
    # 拆出来让 QueryEngine 一次性构造完成，不用 __new__ hack。
    pending_ask_ids: set[str] = set()

    # outbound_queue 让 _ask_user（可能在别的协程栈里 await）能把事件塞进 SSE 流。
    # 用 queue 而不是直接 yield，因为 ask_user 不在 SSE 生成器的调用栈里执行时
    # 没法直接 yield 出去。
    outbound_queue: asyncio.Queue[BaseModel | None] = asyncio.Queue()

    async def _ask_user(
        decision: AskDecision,
        tool: BaseTool,
        tool_input: dict[str, Any],
    ) -> bool:
        """SSE 推 ask 事件 → await pending → 收前端 POST → 返回布尔值。"""
        ask_id = uuid.uuid4().hex
        pending = _PendingAsk()
        _pending_asks[ask_id] = pending
        pending_ask_ids.add(ask_id)

        # 把 ask 事件塞进 outbound_queue，让 SSE 协程发出去
        await outbound_queue.put(
            _AskEvent(
                ask_id=ask_id,
                tool_name=tool.name,
                tool_input=tool_input,
                reason=decision.reason,
            )
        )

        try:
            await asyncio.wait_for(pending.event.wait(), timeout=_ASK_TIMEOUT_SECONDS)
            return pending.answer
        except asyncio.TimeoutError:
            # 超时兜底拒绝：fail-closed，避免长期悬挂
            return False
        finally:
            _pending_asks.pop(ask_id, None)
            pending_ask_ids.discard(ask_id)

    engine = QueryEngine(
        config=config,
        tools=tools,
        permission_manager=permission_mgr,
        ask_user=_ask_user,
    )
    # 把前端带过来的历史消息灌进去。QueryEngine._messages 是私有字段，
    # 但这里是 API 层组装 stateless 会话的唯一合理入口，走 setattr 是可接受的。
    if req.messages:
        # 直接替换而不是 extend——前端持有的历史就是权威版本
        engine._messages = list(req.messages)

    running = _RunningEngine(engine=engine, pending_ask_ids=pending_ask_ids)
    _running[run_id] = running

    async def _drive_engine() -> None:
        """后台任务：跑 engine.submit_message，把所有事件 forward 到 outbound_queue。
        结束后往队列塞 None 作为哨兵，通知 SSE 生成器可以关闭。"""
        try:
            async for event in running.engine.submit_message(req.prompt):
                await outbound_queue.put(event)
        except Exception as exc:  # noqa: BLE001
            await outbound_queue.put(_ErrorEvent(message=str(exc)))
        finally:
            await outbound_queue.put(None)

    async def _sse_generator() -> AsyncIterator[str]:
        # 先推 stream_start 让前端记住 run_id
        yield _sse_pack(_StreamStartEvent(run_id=run_id))

        driver_task = asyncio.create_task(_drive_engine())
        try:
            while True:
                event = await outbound_queue.get()
                if event is None:
                    break
                yield _sse_pack(event)
        finally:
            # 客户端主动断开时确保后台任务被取消并唤醒所有挂起 ask
            if not driver_task.done():
                running.engine.abort()
                _release_pending_asks(running, approved=False)
                try:
                    await asyncio.wait_for(driver_task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    driver_task.cancel()
            _running.pop(run_id, None)

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 避免 Nginx 等反代做缓冲导致事件被批量下发
            "X-Accel-Buffering": "no",
        },
    )


# ── /ask/{id}/answer ──────────────────────────────────────────────────────


@app.post("/ask/{ask_id}/answer")
async def ask_answer(ask_id: str, body: AskAnswer) -> dict[str, str]:
    """前端对某次 Ask 事件的响应。写入答案并唤醒等待中的 ask_user 回调。"""
    pending = _pending_asks.get(ask_id)
    if pending is None:
        # 已经超时/被 abort 释放，或本来就不存在
        raise HTTPException(404, f"no pending ask with id {ask_id}")
    pending.answer = body.approved
    pending.event.set()
    return {"status": "ok"}


# ── /chat/{run_id}/abort ──────────────────────────────────────────────────


@app.post("/chat/{run_id}/abort")
async def chat_abort(run_id: str) -> dict[str, str]:
    """中止一次运行：engine.abort() 让 query 循环在下一轮边界退出，
    同时把该运行下所有挂起的 Ask 都释放为拒绝，避免生成器卡住。"""
    running = _running.get(run_id)
    if running is None:
        raise HTTPException(404, f"no running chat with id {run_id}")
    running.engine.abort()
    _release_pending_asks(running, approved=False)
    return {"status": "aborting"}


def _release_pending_asks(running: _RunningEngine, approved: bool) -> None:
    """把某个运行下所有挂起 Ask 都 set 答案 + set event，让 ask_user 回调醒过来
    并返回。abort 或 SSE 断连时调用。"""
    for ask_id in list(running.pending_ask_ids):
        pending = _pending_asks.get(ask_id)
        if pending is not None:
            pending.answer = approved
            pending.event.set()
