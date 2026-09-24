"""Agent 核心查询循环，负责流式调用 LLM 并递归处理工具调用。

本模块是整个 Agent 系统的心脏。query() 是对外唯一入口，内部用
"调用模型 → 执行工具 → 追加结果 → 继续"的 while 循环，直到模型不再
请求工具或达到最大轮次为止。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

import anthropic

from core.models import (
    MessageCompleteEvent,
    QueryCompleteEvent,
    QueryParams,
    StreamEvent,
    StreamRequestStartEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from permissions.rules import AskDecision

if TYPE_CHECKING:
    from permissions.manager import PermissionManager
    from tools.base import BaseTool


# ── Ask 决策的用户响应回调 ────────────────────────────────────────────────

# 当权限系统判定为 Ask 时由 query() 调用。返回 True 表示允许执行本次工具调用，
# 返回 False 表示拒绝。None 语义等价于 False。
# 回调是 async 以支持"人类通过 SSE/WebSocket 回来点确认"的异步场景，同步
# input() 的 CLI 场景可以包一层 async 适配器（见 scripts/demo_query.py）。
AskUserCallback = Callable[
    [AskDecision, "BaseTool", dict[str, Any]],
    Awaitable[bool],
]


# ── 内部可变状态（dataclass：循环中频繁替换，不需要序列化） ──────────────


@dataclass
class QueryState:
    """query() 循环迭代间共享的可变状态。

    每次 continue 时整体替换（state = QueryState(...)），
    避免多处零散赋值导致状态不一致。

    仅为本文件内部使用，不作为对外契约暴露到 core.models。

    Args:
        messages: 随工具调用追加而增长的消息列表。
        turn_count: 已完成的工具调用轮次，用于 max_turns 判断。
    """

    messages: list[dict[str, Any]]
    turn_count: int = 0


# ── 核心循环 ──────────────────────────────────────────────────────────────


async def query(
    params: QueryParams,
    tools: list[BaseTool],
    permission_manager: PermissionManager,
    abort_event: asyncio.Event | None = None,
    ask_user: AskUserCallback | None = None,
) -> AsyncIterator[StreamEvent]:
    """Agent 查询入口，流式 yield 每个事件直到对话结束。

    调用方通过 async for 消费事件流，可实时渲染文本、展示工具调用、更新 token 统计等，无需等待整轮完成。
    工具列表和权限管理器从外部注入而非放在 QueryParams，原因是两者包含不可序列化的对象，不适合走 Pydantic 校验。

    Args:
        params: 包含消息历史、模型配置等可序列化的查询参数。
        tools: 本轮可用的工具实例列表。
        permission_manager: 工具执行前的权限决策器。
        abort_event: 可选的中止信号，QueryEngine 传入用于响应用户中断。
            循环在每轮开头 check；set 后当前轮 API 请求返回后即结束。
        ask_user: 可选的 Ask 决策回调。权限判定为 Ask 时被调用，返回 True
            则本次工具调用继续执行；返回 False 或未提供该回调时，Ask 会被
            当作拒绝返回给模型（保持向后兼容）。

    Yields:
        StreamEvent 的各子类型，顺序为：
        StreamRequestStartEvent → TextDeltaEvent* → ToolUseEvent* →
        ToolResultEvent* → MessageCompleteEvent, 循环直至结束；
        循环结束后 yield 一个 QueryCompleteEvent，携带最终消息历史。

    Raises:
        anthropic.APIError: API 调用失败时透传原始异常。
    """
    state = QueryState(messages=list(params.messages))
    client = anthropic.AsyncAnthropic(
        api_key=params.api_key,
        base_url=params.base_url,
    )
    tool_schemas = [t.to_api_schema() for t in tools]
    stopped_reason: str = "end_turn"

    while True:
        # 中止在轮次边界响应：粒度足够，避免中断已发出的 API 请求造成状态混乱
        if abort_event is not None and abort_event.is_set():
            stopped_reason = "aborted"
            break

        yield StreamRequestStartEvent()

        pending_tool_uses: list[dict[str, Any]] = []
        accumulated_text = ""
        stop_reason = "end_turn"
        usage: dict[str, int] = {}

        # 流式调用 API，按事件类型处理：文本增量、工具调用、token 统计等
        async with client.messages.stream(
            model=params.model,
            max_tokens=params.max_tokens,
            system=params.system_prompt,
            messages=state.messages,
            tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
        ) as stream:
            async for event in stream:
                # 处理文本增量事件：实时推流给前端
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        accumulated_text += event.delta.text
                        yield TextDeltaEvent(text=event.delta.text)

                # 处理消息中间结果：记录最终的停止原因
                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason or "end_turn"

                # 处理消息流结束：提取最终内容和 token 统计
                elif event.type == "message_stop":
                    final_msg = await stream.get_final_message()
                    usage = {
                        "input_tokens": final_msg.usage.input_tokens,
                        "output_tokens": final_msg.usage.output_tokens,
                        "cache_read_input_tokens": getattr(
                            final_msg.usage, "cache_read_input_tokens", 0
                        ),
                    }
                    # 提取工具调用块，缓存待执行
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

        # 构建助手消息内容：保存文本和工具调用块到消息历史
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

        # 更新状态：追加本轮对话，增加轮次计数
        state = QueryState(
            messages=state.messages + [{"role": "assistant", "content": assistant_content}],
            turn_count=state.turn_count + 1,
        )

        yield MessageCompleteEvent(stop_reason=stop_reason, usage=usage)

        # 判断是否需要继续：模型未请求工具或工具列表为空时结束
        if stop_reason != "tool_use" or not pending_tool_uses:
            stopped_reason = "end_turn"
            # stop_reason 不是 tool_use 却带回 tool_use block 的现实场景是
            # max_tokens 截断：响应被砍断，但已流出的 tool_use block 是完整的。
            # 这条路径的历史同样会被调用方吸收并重放，所以也要补齐 tool_result。
            if pending_tool_uses:
                async for event in _emit_synthetic_tool_results(
                    pending_tool_uses,
                    f"本轮响应因 {stop_reason} 提前结束，工具未执行",
                    state,
                ):
                    yield event
            break

        # 检查轮次限制：超过最大轮次时强制结束
        # 此时刚 emit 的 assistant 消息里已经有 tool_use blocks，Anthropic
        # API 强约束要求 tool_use 必须紧跟着 tool_result；如果就这样 break，
        # 下次调用方拿这段 messages 重放会被 API 400 拒绝。为每个未执行的
        # tool_use 合成一条"因轮次上限跳过"的 tool_result，保持消息序列合规。
        if params.max_turns is not None and state.turn_count >= params.max_turns:
            stopped_reason = "max_turns"
            async for event in _emit_synthetic_tool_results(
                pending_tool_uses,
                "因达到 max_turns 上限，工具未执行",
                state,
            ):
                yield event
            # _emit_synthetic_tool_results 内部已经把 tool_results 追加进
            # state.messages，这里不再重复追加
            break

        # 执行本轮所有工具调用，批量收集结果后一次性追加为 user 消息
        # 原因：Anthropic API 要求工具结果必须作为 user 消息追加
        tool_results: list[dict[str, Any]] = []
        for tool_use in pending_tool_uses:
            result_content, is_error = await _execute_tool(
                tool_use, tools, permission_manager, ask_user
            )
            # 实时推流工具执行结果给前端
            yield ToolResultEvent(
                tool_use_id=tool_use["id"],
                tool_name=tool_use["name"],
                content=result_content,
                is_error=is_error,
            )
            # 缓存结果用于消息历史
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": result_content,
                "is_error": is_error,
            })

        # 更新状态：追加工具结果消息，继续下一轮
        state = QueryState(
            messages=state.messages + [{"role": "user", "content": tool_results}],
            turn_count=state.turn_count,
        )

    # 循环退出后 yield 最终事件，让上层拿到完整消息历史（供多轮会话继续用）
    yield QueryCompleteEvent(
        final_messages=state.messages,
        total_turns=state.turn_count,
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )


async def _execute_tool(
    tool_use: dict[str, Any],
    tools: list[BaseTool],
    permission_manager: PermissionManager,
    ask_user: AskUserCallback | None = None,
) -> tuple[str, bool]:
    """执行单个工具调用，返回结果文本和是否出错的标志。

    执行前先通过 permission_manager 做权限校验。三种决策的处理：
      - Allow：直接进入 validate_input → execute
      - Deny：返回拒绝原因给模型
      - Ask：如果提供了 ask_user 回调，调用它；用户同意则等价 Allow，
        否则等价 Deny。ask_user 为 None 时 Ask 一律视为 Deny，保留
        原有向后兼容行为。

    拒绝时返回拒绝原因而不抛异常，让模型感知并自行决策。

    Args:
        tool_use: 包含 id、name、input 的工具调用描述。
        tools: 可用工具列表，用于按名称查找目标工具。
        permission_manager: 权限决策器。
        ask_user: Ask 决策的用户响应回调，参考 AskUserCallback 类型别名。

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

    if isinstance(decision, AskDecision):
        # 有回调则真的问人；没回调时保留旧行为——Ask 当拒绝，仍是安全的
        # fail-closed 默认（对应权限系统"没人在场就不能自动放行"的语义）
        if ask_user is None:
            return f"权限拒绝（需要用户确认但未提供回调）：{decision.reason}", True
        approved = await ask_user(decision, tool, tool_input)
        if not approved:
            return f"用户拒绝执行：{decision.reason}", True
        # approved 之后 fallthrough 到 validate_input + execute
    elif decision.behavior != "allow":
        # DenyDecision
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


async def _emit_synthetic_tool_results(
    pending_tool_uses: list[dict[str, Any]],
    reason: str,
    state: QueryState,
) -> AsyncIterator[StreamEvent]:
    """为未执行的 tool_use blocks 合成对应的 tool_result 事件与消息条目。

    Anthropic API 强约束：assistant 消息里的每个 tool_use block，紧跟着的
    user 消息必须包含所有对应的 tool_result。循环里有两处 break 会留下
    "已 emit tool_use 但不会执行"的残局：max_turns 上限，以及 stop_reason
    不是 tool_use 却带回 tool_use block（max_tokens 截断）。两处都要补齐，
    否则下次调用方带着这段 messages 重放会被 API 400 拒绝。

    abort 与异常不需要走这里：abort 在轮次开头检查，那一刻 messages 必定
    以 tool_result 消息结尾；异常时本函数所在循环不会 yield
    QueryCompleteEvent，外层吸收不到这段历史（见 core/engine.py）。

    本函数以 fail-closed 的方式补齐：为每个 pending tool_use 生成 is_error=True
    的 tool_result（内容是给定的 reason），既 yield 事件让前端 timeline 可见，
    也直接改写 state.messages 追加对应的 user 消息。

    Args:
        pending_tool_uses: 已 emit 但尚未执行的 tool_use 列表（含 id/name/input）。
        reason: 展示给模型/前端的跳过原因，比如 "因达到 max_turns 上限"。
        state: 当前 QueryState，函数就地在 messages 里追加合成的 user 消息。
    """
    if not pending_tool_uses:
        return

    synthetic_results: list[dict[str, Any]] = []
    for tool_use in pending_tool_uses:
        yield ToolResultEvent(
            tool_use_id=tool_use["id"],
            tool_name=tool_use["name"],
            content=reason,
            is_error=True,
        )
        synthetic_results.append({
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": reason,
            "is_error": True,
        })

    # 直接改写 state.messages——本函数被调用时循环马上 break，之后 state 只
    # 用于 QueryCompleteEvent.final_messages，追加一次即可保持消息序列合规
    state.messages.append({"role": "user", "content": synthetic_results})
