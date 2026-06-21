"""Bash 工具，在本地 shell 中执行命令并流式返回输出。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any, AsyncIterator

from tools.base import BaseTool, ToolUseContext

_SHELL = ["powershell", "-Command"] if sys.platform == "win32" else ["bash", "-c"]
_DEFAULT_TIMEOUT = 30


def _run_command_sync(shell: list[str], command: str, timeout: int) -> tuple[str, int]:
    """同步执行 shell 命令，返回 (完整输出, 退出码)。

    仅作为降级方案：Windows SelectorEventLoop 不支持异步子进程时，
    由 execute() 通过 run_in_executor 在线程池中调用。
    """
    result = subprocess.run(
        shell + [command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return result.stdout.decode(errors="replace"), result.returncode


class BashTool(BaseTool):
    """在本地 shell 中执行命令，流式 yield 每行输出。

    优先使用异步子进程（逐行流式）；Windows SelectorEventLoop 下自动降级为
    线程池执行（命令完成后一次性返回），保证跨平台兼容。
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

            async for line in process.stdout:  # type: ignore[union-attr]
                yield line.decode(errors="replace")

            await asyncio.wait_for(process.wait(), timeout=timeout)

            if process.returncode and process.returncode != 0:
                yield f"\n[退出码: {process.returncode}]\n"

        except NotImplementedError:
            # Windows SelectorEventLoop 不支持异步子进程，降级到线程池
            try:
                loop = asyncio.get_running_loop()
                output, returncode = await loop.run_in_executor(
                    None, _run_command_sync, _SHELL, command, timeout
                )
                yield output
                if returncode != 0:
                    yield f"\n[退出码: {returncode}]\n"
            except subprocess.TimeoutExpired:
                yield f"[命令超时：{timeout} 秒后终止]\n"

        except asyncio.TimeoutError:
            yield f"[命令超时：{timeout} 秒后终止]\n"

        except FileNotFoundError:
            yield f"[Shell 未找到：{_SHELL[0]}]\n"
