"""端到端手动烟测：跑一次真实 API，观察 query() 的事件流。

使用方式（在项目根目录执行）：

    uv run python scripts/demo_query.py           # 全部场景
    uv run python scripts/demo_query.py chat      # 只跑纯对话
    uv run python scripts/demo_query.py tools     # 只跑工具调用循环
    uv run python scripts/demo_query.py session   # 只跑 QueryEngine 多轮会话
    uv run python scripts/demo_query.py ask       # 只跑 Ask 闭环（子命令级权限确认）

需要 .env 里配置 ANTHROPIC_API_KEY；可选 MODEL_ID 覆盖默认模型。

pytest 覆盖的是逻辑正确性；本脚本覆盖的是"真接一次 API 的事件流长什么样"，
两者互补，不重复。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# 允许从项目根目录直接 `python scripts/demo_query.py` 运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from core.engine import QueryEngine
from core.models import (
    EngineConfig,
    MessageCompleteEvent,
    QueryParams,
    StreamRequestStartEvent,
    SubmitResultEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from core.query import query
from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import AskDecision, PermissionMode
from tools.base import BaseTool
from tools.bash import BashTool


load_dotenv(PROJECT_ROOT / ".env")
API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_ID = os.getenv("MODEL_ID", "claude-sonnet-4-6")


async def cli_ask_user(
    decision: AskDecision,
    tool: BaseTool,
    tool_input: dict[str, Any],
) -> bool:
    """基于 input() 的 Ask 响应回调：把决策原因和工具参数摘要展示给用户。

    这是 Ask 闭环的最小可用交互层——权限系统判为 Ask 时，会在终端里
    以 "[需要确认]" 提示用户按 y/n。让 permissions 三态判断第一次真正
    生效（在此之前 Ask 会被当作拒绝，无人能真正响应）。

    Returns:
        用户按 y/回车 表示同意；其他任何输入表示拒绝。
    """
    print()  # 换行避开可能被 TextDelta 覆盖的最后一行
    print("─" * 60)
    print(f"[需要确认] {decision.reason}")
    print(f"  工具：{tool.name}")
    preview = repr(tool_input)
    if len(preview) > 200:
        preview = preview[:200] + "…"
    print(f"  参数：{preview}")
    # asyncio.to_thread 避免 input() 阻塞事件循环
    answer = await asyncio.to_thread(input, "  允许执行？[y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def demo_chat() -> None:
    """场景 1：无工具的纯对话，验证流式文本增量。"""
    print("\n=== 场景 1：纯对话 ===")
    params = QueryParams(
        messages=[{"role": "user", "content": "用一句话介绍你自己"}],
        system_prompt="你是一个简洁的助手，回答不超过两句话。",
        model=MODEL_ID,
        api_key=API_KEY,
    )
    mgr = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))

    print("模型回复：", end="", flush=True)
    async for event in query(params, tools=[], permission_manager=mgr):
        if isinstance(event, TextDeltaEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, MessageCompleteEvent):
            print(f"\n[stop_reason={event.stop_reason}, tokens={event.usage}]")


async def demo_tools() -> None:
    """场景 2：完整的工具调用循环，验证 tool_use → tool_result → 继续对话。"""
    print("\n=== 场景 2：工具调用循环 ===")
    bash = BashTool(timeout=15)
    mgr = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))

    params = QueryParams(
        messages=[
            {
                "role": "user",
                "content": "用 Bash 工具列出当前目录下的 .py 文件，然后告诉我有几个。",
            }
        ],
        system_prompt="你是一个编程助手，可以使用 Bash 工具执行命令。",
        model=MODEL_ID,
        api_key=API_KEY,
        max_turns=5,
    )

    async for event in query(params, tools=[bash], permission_manager=mgr):
        if isinstance(event, StreamRequestStartEvent):
            print("\n--- 新一轮 API 请求 ---")
        elif isinstance(event, TextDeltaEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolUseEvent):
            print(f"\n[工具调用] {event.tool_name}({event.tool_input})")
        elif isinstance(event, ToolResultEvent):
            preview = event.content[:200].replace("\n", "\\n")
            print(f"[工具结果] is_error={event.is_error} | {preview}")
        elif isinstance(event, MessageCompleteEvent):
            print(f"\n[完成] stop_reason={event.stop_reason}")


async def demo_session() -> None:
    """场景 3：QueryEngine 多轮会话，验证消息历史跨调用累积。

    连续两次 submit_message：第二次应能引用第一次说过的内容，
    结束后打印累计 usage 与消息数，确认状态正确累加。
    """
    print("\n=== 场景 3：QueryEngine 多轮会话 ===")
    config = EngineConfig(
        system_prompt="你是一个简洁的助手，回答不超过一句话。",
        model=MODEL_ID,
        api_key=API_KEY,
        max_turns_per_submit=3,
    )
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))
    engine = QueryEngine(config, tools=[], permission_manager=manager)

    async def run_turn(prompt: str, label: str) -> None:
        print(f"\n[{label}] 用户：{prompt}")
        print(f"[{label}] 模型：", end="", flush=True)
        async for event in engine.submit_message(prompt):
            if isinstance(event, TextDeltaEvent):
                print(event.text, end="", flush=True)
            elif isinstance(event, SubmitResultEvent):
                print(
                    f"\n[{label}] 结束：subtype={event.subtype}, "
                    f"turns={event.num_turns}, usage={event.total_usage}"
                )

    await run_turn("我叫小明。", "第 1 轮")
    await run_turn("我叫什么名字？", "第 2 轮")

    print(f"\n累计消息数：{len(engine.messages)}")
    print(f"累计 usage：{engine.total_usage}")


async def demo_ask() -> None:
    """场景 4：Ask 闭环 —— 权限系统三态判断第一次真正生效。

    default 模式 + allow=Bash(git:*) 规则：模型执行 `git status` 会被子命令
    级 allow 规则放行，无需询问；执行 `git status && rm -rf /tmp/x` 时因为
    `rm` 子命令没有 allow 规则，会触发 Ask 让用户在终端里 y/n 确认。
    """
    print("\n=== 场景 4：Ask 闭环 ===")
    print("规则：allow=Bash(git:*)。默认模式下，非 git 子命令会触发 Ask，")
    print("      而 git 子命令则被直接放行——观察对比。\n")

    config = EngineConfig(
        system_prompt=(
            "你是一个编程助手，会按用户要求分步骤执行命令。"
            "每个命令都通过 Bash 工具执行，不要合并成一条大命令。"
        ),
        model=MODEL_ID,
        api_key=API_KEY,
        max_turns_per_submit=5,
    )
    context = PermissionContext.from_rule_strings(
        mode=PermissionMode.DEFAULT,
        allow=["Bash(git:*)"],
    )
    manager = PermissionManager(context)
    engine = QueryEngine(
        config,
        tools=[BashTool(timeout=15)],
        permission_manager=manager,
        ask_user=cli_ask_user,
    )

    async for event in engine.submit_message(
        "请先运行 `git status` 查看当前分支状态，然后运行 `echo hello` 打印一句话。"
    ):
        if isinstance(event, StreamRequestStartEvent):
            print("\n--- 新一轮 API 请求 ---")
        elif isinstance(event, TextDeltaEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolUseEvent):
            print(f"\n[工具调用] {event.tool_name}({event.tool_input})")
        elif isinstance(event, ToolResultEvent):
            preview = event.content[:200].replace("\n", "\\n")
            print(f"[工具结果] is_error={event.is_error} | {preview}")
        elif isinstance(event, SubmitResultEvent):
            print(
                f"\n[结束] subtype={event.subtype}, turns={event.num_turns}, "
                f"denials={len(event.permission_denials)}"
            )


async def main() -> None:
    if not API_KEY:
        print("未找到 ANTHROPIC_API_KEY，请在项目根目录创建 .env 文件", file=sys.stderr)
        sys.exit(1)

    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"使用模型：{MODEL_ID}")

    if which in {"all", "chat"}:
        await demo_chat()
    if which in {"all", "tools"}:
        await demo_tools()
    if which in {"all", "session"}:
        await demo_session()
    if which in {"all", "ask"}:
        await demo_ask()


if __name__ == "__main__":
    asyncio.run(main())
