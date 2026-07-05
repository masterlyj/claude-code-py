"""Agent 核心的对外数据契约：查询参数、会话配置与流式事件。

query.py / engine.py 是主循环和会话编排层的实现，本文件是它们与外部之间的契约层：
FastAPI 端点、SSE 序列化、前端事件分发、日志采集、测试断言都会 import 这里的类型。

query.py / engine.py 内部的可变状态（QueryState、QueryEngine 的 _messages 等）
不属于对外契约，不在此文件——它们是实现细节，泄漏出去反而增加耦合。

对外暴露：
  QueryParams              — 调用 query() 所需的全部参数
  EngineConfig             — 构造 QueryEngine 所需的配置
  StreamRequestStartEvent  — API 请求开始事件
  TextDeltaEvent           — 文本增量事件
  ToolUseEvent             — 工具调用事件
  ToolResultEvent          — 工具结果事件
  MessageCompleteEvent     — 单轮完成事件
  QueryCompleteEvent       — query() 循环结束事件（携带最终消息历史）
  SubmitResultEvent        — 一次 submit_message() 的最终结果事件
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


class EngineConfig(BaseModel):
    """构造 QueryEngine 所需的配置集合。

    与 QueryParams 分开的原因：EngineConfig 描述整个会话的稳定参数（模型、系统提示、
    API Key），QueryParams 描述一次 query() 调用的即时参数（本轮消息、本轮轮次上限）。
    QueryEngine 在每次 submit_message() 时基于 EngineConfig 组装出一个 QueryParams。

    Args:
        system_prompt: 会话级系统提示，多轮共享。
        model: 调用的模型 ID。
        max_tokens: 单次模型响应最大 token 数。
        max_turns_per_submit: 单次 submit_message() 的工具调用轮次上限；
            None 表示不限，防止死循环。
        api_key: Anthropic API Key，None 时从环境变量 ANTHROPIC_API_KEY 读取。
    """

    system_prompt: str
    model: str = "claude-sonnet-4-6"
    max_tokens: int = Field(default=8096, gt=0)
    max_turns_per_submit: int | None = Field(default=None, gt=0)
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
        tool_use_id: 本次工具调用的唯一 ID,用于关联 tool_result。
        tool_name: 工具名称。
        tool_input: 工具调用参数。
    """

    type: Literal["tool_use"] = "tool_use"
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]


class ToolResultEvent(BaseModel):
    """工具执行完毕,结果将追加回对话。

    Args:
        tool_use_id: 对应 ToolUseEvent 的 ID。
        tool_name: 工具名称,便于日志和 UI 展示。
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
        stop_reason: 停止原因,"end_turn" 表示正常结束,"tool_use" 表示需要执行工具。
        usage: token 用量统计,含 input_tokens / output_tokens / cache_read_input_tokens。
    """

    type: Literal["message_complete"] = "message_complete"
    stop_reason: str
    usage: dict[str, int]


class QueryCompleteEvent(BaseModel):
    """query() 整个循环结束时 yield 的最后一个事件。

    携带最终消息历史与循环统计，让上层调用者（如 QueryEngine）能吸收本次
    query() 期间新增的 assistant / tool_result 消息，作为下轮对话的起点。

    Args:
        final_messages: 循环结束时的完整消息历史（含初始输入 + 本次新增）。
        total_turns: 本次 query() 完成的工具调用轮次数。
        stopped_reason: 停止原因，"end_turn" 表示模型主动结束，
            "max_turns" 表示达到轮次上限，"aborted" 表示被外部中止。
    """

    type: Literal["query_complete"] = "query_complete"
    final_messages: list[dict[str, Any]]
    total_turns: int
    stopped_reason: Literal["end_turn", "max_turns", "aborted"]


class SubmitResultEvent(BaseModel):
    """一次 submit_message() 结束时 yield 的最终事件。

    汇总本次调用的结果统计，供调用方展示或记录。

    Args:
        subtype: 结束子类型，"success" 正常完成，"max_turns" 达到轮次上限，"aborted" 被中止。
        num_turns: 本次 submit_message() 中完成的工具调用轮次数。
        total_usage: 会话累计的 token 用量（跨 submit_message 调用累加）。
        permission_denials: 本次 submit_message() 期间发生的权限拒绝记录，
            每条含 tool_name / tool_input / reason。
    """

    type: Literal["submit_result"] = "submit_result"
    subtype: Literal["success", "max_turns", "aborted"]
    num_turns: int
    total_usage: dict[str, int]
    permission_denials: list[dict[str, Any]]


# Pydantic discriminated union，序列化时自动带 type 字段，前端可直接按 type 分发
StreamEvent = Annotated[
    StreamRequestStartEvent
    | TextDeltaEvent
    | ToolUseEvent
    | ToolResultEvent
    | MessageCompleteEvent
    | QueryCompleteEvent
    | SubmitResultEvent,
    Field(discriminator="type"),
]
