"""Bash 工具，在本地 shell 中执行命令并流式返回输出。"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, AsyncIterator

from tools.base import BaseTool, ToolUseContext

_SHELL = ["powershell", "-Command"] if sys.platform == "win32" else ["bash", "-c"]
_DEFAULT_TIMEOUT = 30


class BashTool(BaseTool):
    """在本地 shell 中执行命令，逐行流式 yield 输出。

    Windows 上通过 powershell 执行，其他平台通过 bash 执行。
    依赖 asyncio 原生异步子进程（Python 3.8+ 起 Windows 默认使用
    ProactorEventLoop，无需额外降级处理）。
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "Bash"

    @property
    def description(self) -> str:
        return (
            "在本地 shell 中执行命令。"
            "适用于文件操作、运行脚本、查看目录结构等任务。"
            "stdout 和 stderr 合并返回。"
            f"超时限制为 {self._timeout} 秒。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令。",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"可选，覆盖默认超时（秒），最大 {_DEFAULT_TIMEOUT * 2}。",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """执行 shell 命令，逐行 yield 输出。

        Args:
            tool_input: 包含 command（必填）和可选 timeout 的参数字典。
            context: 工具执行上下文（预留给权限扩展）。

        Yields:
            命令输出的文本片段。超时或异常时 yield 错误描述。
        """
        command = tool_input["command"]
        timeout = min(tool_input.get("timeout", self._timeout), _DEFAULT_TIMEOUT * 2)

        try:
            process = await asyncio.create_subprocess_exec(
                *_SHELL,
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None

            async for line in process.stdout:
                yield line.decode(errors="replace")

            await asyncio.wait_for(process.wait(), timeout=timeout)

            if process.returncode and process.returncode != 0:
                yield f"\n[退出码: {process.returncode}]\n"

        except asyncio.TimeoutError:
            yield f"[命令超时：{timeout} 秒后终止]\n"

        except FileNotFoundError:
            yield f"[Shell 未找到：{_SHELL[0]}]\n"
