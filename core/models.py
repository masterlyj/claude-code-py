"""Agent 核心的对外数据契约：查询参数与流式事件。

query.py 是主循环的实现，本文件是它与外部之间的契约层：
FastAPI 端点、SSE 序列化、前端事件分发、日志采集、测试断言
都会 import 这里的类型，因此这些类型必须与主循环解耦地存在。

query.py 内部的可变状态（QueryState）不属于对外契约，
不在此文件——它是循环的实现细节，泄漏出去反而增加耦合。

对外暴露：
  QueryParams              — 调用 query() 所需的全部参数
  StreamRequestStartEvent  — API 请求开始事件
  TextDeltaEvent           — 文本增量事件
  ToolUseEvent             — 工具调用事件
  ToolResultEvent          — 工具结果事件
  MessageCompleteEvent     — 单轮完成事件
  StreamEvent              — 以上所有事件的联合类型（discriminated union）
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


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
