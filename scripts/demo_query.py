"""端到端手动烟测：跑一次真实 API，观察 query() 的事件流。

使用方式（在项目根目录执行）：

    uv run python scripts/demo_query.py           # 两个场景都跑
    uv run python scripts/demo_query.py chat      # 只跑纯对话
    uv run python scripts/demo_query.py tools     # 只跑工具调用循环

需要 .env 里配置 ANTHROPIC_API_KEY；可选 MODEL_ID 覆盖默认模型。

pytest 覆盖的是逻辑正确性；本脚本覆盖的是"真接一次 API 的事件流长什么样"，
两者互补，不重复。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 允许从项目根目录直接 `python scripts/demo_query.py` 运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from core.models import (
    MessageCompleteEvent,
    QueryParams,
    StreamRequestStartEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from core.query import query
from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import PermissionMode
from tools.bash import BashTool


load_dotenv(PROJECT_ROOT / ".env")
API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_ID = os.getenv("MODEL_ID", "claude-sonnet-4-6")


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


if __name__ == "__main__":
    asyncio.run(main())
