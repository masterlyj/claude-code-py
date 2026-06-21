"""Agent 核心查询循环，负责流式调用 LLM 并递归处理工具调用。

本模块是整个 Agent 系统的心脏。query() 是对外唯一入口，
内部通过 _query_loop() 实现"调用模型 → 执行工具 → 追加结果 → 继续"的循环，
直到模型不再请求工具或达到最大轮次为止。

对外暴露：
  QueryParams          — 调用 query() 所需的全部参数（Pydantic，支持 FastAPI 接入）
  QueryState           — 循环迭代间共享的可变状态（dataclass，内部用）
  StreamRequestStartEvent — API 请求开始事件
  TextDeltaEvent       — 文本增量事件
  ToolUseEvent         — 工具调用事件
  ToolResultEvent      — 工具结果事件
  MessageCompleteEvent — 单轮完成事件
  StreamEvent          — 以上所有事件的联合类型
  query                — 主入口，异步生成器，逐事件 yield 给调用方
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Literal

import anthropic
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from permissions.manager import PermissionManager
    from tools.base import BaseTool


# ── 请求参数（Pydantic：FastAPI 可直接接收，支持序列化和校验） ─────────────


class QueryParams(BaseModel):
    """query() 的输入参数集合。

    使用 Pydantic 以便 FastAPI 端点直接接收和校验，
    同时支持 .model_dump() 序列化用于日志和追踪。

    Args:
        messages: 当前对话的完整消息历史（user / assistant 交替）。
        system_prompt: 注入给模型的系统提示文本。
        model: 调用的模型 ID。
        max_tokens: 单次响应最大 token 数。
        max_turns: 工具调用最大轮次，None 表示不限。
        api_key: Anthropic API Key，None 时从环境变量 ANTHROPIC_API_KEY 读取。
    """

    messages: list[dict[str, Any]]
    system_prompt: str
    model: str = "claude-sonnet-4-6"
    max_tokens: int = Field(default=8096, gt=0)
    max_turns: int | None = Field(default=None, gt=0)
    api_key: str | None = None

    model_config = {"arbitrary_types_allowed": True}


# ── 内部可变状态（dataclass：循环中频繁替换，不需要序列化） ──────────────


@dataclass
class QueryState:
    """_query_loop() 迭代间共享的可变状态。

    每次 continue 时整体替换（state = QueryState(...)），
    避免多处零散赋值导致状态不一致。

    Args:
        messages: 随工具调用追加而增长的消息列表。
        turn_count: 已完成的工具调用轮次，用于 max_turns 判断。
    """

    messages: list[dict[str, Any]]
    turn_count: int = 0


# ── 流式事件类型（Pydantic：通过 SSE 序列化推给前端） ────────────────────


class StreamRequestStartEvent(BaseModel):
    """标志一次 API 请求开始，供调用方更新 UI 加载状态。"""

    type: Literal["stream_request_start"] = "stream_request_start"


class TextDeltaEvent(BaseModel):
    """模型输出的文本增量片段。

    Args:
        text: 本次增量文本内容。
    """

    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolUseEvent(BaseModel):
    """模型请求调用一个工具。

    Args:
        tool_use_id: 本次工具调用的唯一 ID，用于关联 tool_result。
        tool_name: 工具名称。
        tool_input: 工具调用参数。
    """

    type: Literal["tool_use"] = "tool_use"
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]


class ToolResultEvent(BaseModel):
    """工具执行完毕，结果将追加回对话。

    Args:
        tool_use_id: 对应 ToolUseEvent 的 ID。
        tool_name: 工具名称，便于日志和 UI 展示。
        content: 工具执行结果文本。
        is_error: 是否为执行错误结果。
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    tool_name: str
    content: str
    is_error: bool = False


class MessageCompleteEvent(BaseModel):
    """一轮完整的模型响应结束。

    Args:
        stop_reason: 停止原因，"end_turn" 表示正常结束，"tool_use" 表示需要执行工具。
        usage: token 用量统计，含 input_tokens / output_tokens / cache_read_input_tokens。
    """

    type: Literal["message_complete"] = "message_complete"
    stop_reason: str
    usage: dict[str, int]


# Pydantic discriminated union，序列化时自动带 type 字段，前端可直接按 type 分发
StreamEvent = Annotated[
    StreamRequestStartEvent
    | TextDeltaEvent
    | ToolUseEvent
    | ToolResultEvent
    | MessageCompleteEvent,
    Field(discriminator="type"),
]


# ── 核心循环 ──────────────────────────────────────────────────────────────


async def query(
    params: QueryParams,
    tools: list[BaseTool],
    permission_manager: PermissionManager,
) -> AsyncIterator[StreamEvent]:
    """Agent 查询入口，流式 yield 每个事件直到对话结束。

    调用方通过 async for 消费事件流，可实时渲染文本、展示工具调用、
    更新 token 统计等，无需等待整轮完成。

    工具列表和权限管理器从外部注入而非放在 QueryParams，
    原因是两者包含不可序列化的对象，不适合走 Pydantic 校验。

    Args:
        params: 包含消息历史、模型配置等可序列化的查询参数。
        tools: 本轮可用的工具实例列表。
        permission_manager: 工具执行前的权限决策器。

    Yields:
        StreamEvent 的各子类型，顺序为：
        StreamRequestStartEvent → TextDeltaEvent* → ToolUseEvent* →
        ToolResultEvent* → MessageCompleteEvent，循环直至结束。

    Raises:
        anthropic.APIError: API 调用失败时透传原始异常。
    """
    state = QueryState(messages=list(params.messages))

    async for event in _query_loop(params, tools, permission_manager, state):
        yield event


async def _query_loop(
    params: QueryParams,
    tools: list[BaseTool],
    permission_manager: PermissionManager,
    state: QueryState,
) -> AsyncIterator[StreamEvent]:
    """query() 的内部循环实现，每次迭代对应一轮模型调用。

    Args:
        params: 不可变的查询参数，整个循环期间不变。
        tools: 本轮可用的工具列表。
        permission_manager: 权限决策器。
        state: 跨迭代共享的可变状态，每轮结束后整体替换。

    Yields:
        与 query() 相同的 StreamEvent 序列。
    """
    client = anthropic.AsyncAnthropic(api_key=params.api_key)
    tool_schemas = [t.to_api_schema() for t in tools]

    while True:
        yield StreamRequestStartEvent()

        pending_tool_uses: list[dict[str, Any]] = []
        accumulated_text = ""
        stop_reason = "end_turn"
        usage: dict[str, int] = {}

        async with client.messages.stream(
            model=params.model,
            max_tokens=params.max_tokens,
            system=params.system_prompt,
            messages=state.messages,
            tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
        ) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        accumulated_text += event.delta.text
                        yield TextDeltaEvent(text=event.delta.text)

                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason or "end_turn"

                elif event.type == "message_stop":
                    final_msg = await stream.get_final_message()
                    usage = {
                        "input_tokens": final_msg.usage.input_tokens,
                        "output_tokens": final_msg.usage.output_tokens,
                        "cache_read_input_tokens": getattr(
                            final_msg.usage, "cache_read_input_tokens", 0
                        ),
                    }
                    for block in final_msg.content:
                        if block.type == "tool_use":
                            pending_tool_uses.append({
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            })
                            yield ToolUseEvent(
                                tool_use_id=block.id,
                                tool_name=block.name,
                                tool_input=block.input,
                            )

        # 将本轮助手回复追加到消息历史
        assistant_content: list[dict[str, Any]] = []
        if accumulated_text:
            assistant_content.append({"type": "text", "text": accumulated_text})
        for tool_use in pending_tool_uses:
            assistant_content.append({
                "type": "tool_use",
                "id": tool_use["id"],
                "name": tool_use["name"],
                "input": tool_use["input"],
            })

        state = QueryState(
            messages=state.messages + [{"role": "assistant", "content": assistant_content}],
            turn_count=state.turn_count + 1,
        )

        yield MessageCompleteEvent(stop_reason=stop_reason, usage=usage)

        if stop_reason != "tool_use" or not pending_tool_uses:
            break

        if params.max_turns is not None and state.turn_count >= params.max_turns:
            break

        # 执行所有工具调用，收集结果后一次性追加为 user 消息
        tool_results: list[dict[str, Any]] = []
        for tool_use in pending_tool_uses:
            result_content, is_error = await _execute_tool(
                tool_use, tools, permission_manager
            )
            yield ToolResultEvent(
                tool_use_id=tool_use["id"],
                tool_name=tool_use["name"],
                content=result_content,
                is_error=is_error,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": result_content,
                "is_error": is_error,
            })

        state = QueryState(
            messages=state.messages + [{"role": "user", "content": tool_results}],
            turn_count=state.turn_count,
        )


async def _execute_tool(
    tool_use: dict[str, Any],
    tools: list[BaseTool],
    permission_manager: PermissionManager,
) -> tuple[str, bool]:
    """执行单个工具调用，返回结果文本和是否出错的标志。

    执行前先通过 permission_manager 做权限校验，
    再通过工具自身的 validate_input() 做参数校验，
    拒绝时返回拒绝原因而不抛异常，让模型感知并自行决策。

    Args:
        tool_use: 包含 id、name、input 的工具调用描述。
        tools: 可用工具列表，用于按名称查找目标工具。
        permission_manager: 权限决策器。

    Returns:
        (result_content, is_error) 元组：
        result_content 是执行结果或错误描述；
        is_error 为 True 时告知模型本次调用失败。
    """
    from tools.base import ToolUseContext

    tool_name = tool_use["name"]
    tool_input = tool_use["input"]

    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        return f"未找到工具：{tool_name}", True

    # 权限校验优先于输入校验，避免在无权限时泄露参数细节
    decision = await permission_manager.check(tool, tool_input)
    if decision.behavior != "allow":
        return f"权限拒绝：{decision.reason}", True

    validation = await tool.validate_input(tool_input)
    if not validation.ok:
        return f"参数校验失败：{validation.message}", True

    context = ToolUseContext(
        permission_manager=permission_manager,
        model=tool_name,
        tools=tools,
        session_id="",
    )

    try:
        chunks: list[str] = []
        async for chunk in tool.execute(tool_input, context):
            chunks.append(chunk)
        return "".join(chunks), False
    except Exception as exc:  # noqa: BLE001
        return f"工具执行失败：{exc}", True
