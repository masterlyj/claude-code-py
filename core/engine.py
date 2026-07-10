"""Agent 会话编排层，把单次 query() 循环组合成可持续交互的多轮会话。

query.py 是"跑一轮就走"的原子操作，本模块把它包装成可以多次 submit_message 的
会话对象：
  - 跨调用维护消息历史（每次 submit_message 结束后吸收 query() 的最终消息列表）
  - 累加 token 用量（跨轮次统计供计费/限流用）
  - 追踪权限拒绝（供 UI 展示或 SDK 报告）
  - 响应外部中止（asyncio.Event 在轮次边界生效）

对应原版 src/QueryEngine.ts 的 QueryEngine 类，做了大幅简化：
  ❌ 不实现：MCP、plugins、消息压缩、结构化输出、预算控制、transcript 持久化
  ✅ 保留：会话状态、累计 usage、权限拒绝追踪、中止信号

对外暴露：
  QueryEngine — 会话编排类，一次会话对应一个实例
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator

from core.models import (
    EngineConfig,
    MessageCompleteEvent,
    QueryCompleteEvent,
    QueryParams,
    StreamEvent,
    SubmitResultEvent,
)
from core.query import AskUserCallback, query
from permissions.manager import PermissionManager
from permissions.rules import PermissionDecision

if TYPE_CHECKING:
    from tools.base import BaseTool


class QueryEngine:
    """会话级 Agent 编排器，一次会话对应一个实例。

    实例的状态跨 submit_message() 调用累积：消息历史、token 用量、权限拒绝记录。
    调用方在同一实例上多次 submit_message()，就是多轮对话；换新实例等于新会话。

    Args:
        config: 会话稳定配置（system_prompt / model / api_key 等）。
        tools: 本次会话可用的工具列表，构造后固定。
        permission_manager: 权限决策器；实例内部会包一层用于追踪拒绝记录，
            不影响外部传入对象的状态。

    Attributes:
        messages: 累计消息历史（只读快照访问，请用 self.messages 属性）。
        total_usage: 累计 token 用量。
        permission_denials: 累计权限拒绝记录。
    """

    def __init__(
        self,
        config: EngineConfig,
        tools: list[BaseTool],
        permission_manager: PermissionManager,
        ask_user: AskUserCallback | None = None,
    ) -> None:
        self._config = config
        self._tools = tools
        self._permission_manager = permission_manager
        self._ask_user = ask_user
        self._messages: list[dict[str, Any]] = []
        self._total_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        self._permission_denials: list[dict[str, Any]] = []
        self._abort_event = asyncio.Event()

    @property
    def messages(self) -> list[dict[str, Any]]:
        """当前累计消息历史的拷贝，只读用途。"""
        return list(self._messages)

    @property
    def total_usage(self) -> dict[str, int]:
        """当前累计 token 用量的拷贝，只读用途。"""
        return dict(self._total_usage)

    @property
    def permission_denials(self) -> list[dict[str, Any]]:
        """当前累计权限拒绝记录的拷贝，只读用途。"""
        return list(self._permission_denials)

    def abort(self) -> None:
        """请求中止当前进行中的 submit_message()。

        中止在 query() 循环的轮次边界响应：粒度足够，避免中断已发出的
        API 请求造成状态混乱。调用后本次 submit_message 会尽快 yield
        SubmitResultEvent(subtype="aborted") 并结束。
        """
        self._abort_event.set()

    async def submit_message(
        self,
        prompt: str,
    ) -> AsyncIterator[StreamEvent]:
        """提交一条用户消息，驱动一次 query() 循环，透传其事件流。

        单次调用的生命周期：
          1. 把 prompt 作为 user 消息追加到累计历史
          2. 用累计历史组装 QueryParams，调用 query()
          3. 透传 query() 的所有事件；顺路：
             - MessageCompleteEvent → 累加 total_usage
             - QueryCompleteEvent → 吸收 final_messages 覆盖累计历史
          4. 结束时 yield 一个 SubmitResultEvent 总结本次调用

        Args:
            prompt: 本轮用户输入的文本。

        Yields:
            StreamEvent 的各子类型，最后一个总是 SubmitResultEvent。
        """
        # 重置中止信号，让每次 submit_message 有独立的中断周期
        self._abort_event.clear()
        # 重置本次 submit 的拒绝记录（total_usage 与 messages 跨轮次累加，保留）
        turn_denials_start = len(self._permission_denials)

        # push 用户消息，与助手回复一起构成 query() 的输入
        self._messages.append({"role": "user", "content": prompt})

        # 用装饰后的 permission_manager 传给 query(），追踪本次调用的拒绝
        wrapped_manager = _DenialTrackingPermissionManager(
            self._permission_manager, self._permission_denials
        )

        params = QueryParams(
            messages=self._messages,
            system_prompt=self._config.system_prompt,
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            max_turns=self._config.max_turns_per_submit,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
        )

        result_subtype: str = "success"
        num_turns = 0

        async for event in query(
            params,
            self._tools,
            wrapped_manager,
            self._abort_event,
            ask_user=self._ask_user,
        ):
            if isinstance(event, MessageCompleteEvent):
                self._accumulate_usage(event.usage)

            if isinstance(event, QueryCompleteEvent):
                # 吸收 query() 期间新增的 assistant / tool_result 消息
                self._messages = list(event.final_messages)
                num_turns = event.total_turns
                if event.stopped_reason == "max_turns":
                    result_subtype = "max_turns"
                elif event.stopped_reason == "aborted":
                    result_subtype = "aborted"
                # QueryCompleteEvent 不透传给上层——它是内部同步用的边界事件，
                # 上层用 SubmitResultEvent 拿到更完整的统计
                continue

            yield event

        yield SubmitResultEvent(
            subtype=result_subtype,  # type: ignore[arg-type]
            num_turns=num_turns,
            total_usage=dict(self._total_usage),
            permission_denials=self._permission_denials[turn_denials_start:],
        )

    def _accumulate_usage(self, usage: dict[str, int]) -> None:
        """把单轮 usage 累加进会话总量。"""
        for key, value in usage.items():
            self._total_usage[key] = self._total_usage.get(key, 0) + value


class _DenialTrackingPermissionManager(PermissionManager):
    """内部装饰器：包一层 PermissionManager，把拒绝记录 push 到外部列表。

    这样 query() 用它跟原生 PermissionManager 完全一样，
    QueryEngine 就能收集本次 submit_message 期间发生的所有拒绝。

    继承而非组合是因为 query() 的类型标注要求 PermissionManager 实例。
    """

    def __init__(
        self,
        inner: PermissionManager,
        denials_sink: list[dict[str, Any]],
    ) -> None:
        # 复用 inner 的 context，避免各自持有独立引用导致状态错位
        super().__init__(inner.context)
        self._inner = inner
        self._denials_sink = denials_sink

    async def check(
        self,
        tool: "BaseTool",
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        decision = await self._inner.check(tool, tool_input)
        # 只记录明确的 Deny：Ask 属于"未决"，最终结果取决于 ask_user 的回答，
        # 由 query() 层根据用户回应决定放行或拒绝；此处若把 Ask 也当拒绝记，
        # 会在用户同意的场景里制造误报。
        if decision.behavior == "deny":
            self._denials_sink.append({
                "tool_name": tool.name,
                "tool_input": tool_input,
                "behavior": decision.behavior,
                "reason": decision.reason,
            })
        return decision
