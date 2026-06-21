"""Bash 工具，在本地 shell 中执行命令并流式返回输出。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any, AsyncIterator

from tools.base import BaseTool, ToolUseContext

# Windows 下使用 PowerShell，其他平台使用 bash
_SHELL = ["powershell", "-Command"] if sys.platform == "win32" else ["bash", "-c"]

# 单次命令的超时秒数，防止命令挂住整个 Agent 循环
_DEFAULT_TIMEOUT = 30


def _run_command_sync(shell: list[str], command: str, timeout: int) -> tuple[str, int]:
    """同步执行 shell 命令，返回 (完整输出, 退出码)。

    仅作为降级方案：当运行环境的 event loop 不支持异步子进程时
    （如 Windows SelectorEventLoop），由 execute() 通过 run_in_executor
    在线程池中调用。代价是无法逐行流式输出。
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

    优先使用异步子进程实现真正的逐行流式输出；
    当运行环境不支持异步子进程时（如 Windows SelectorEventLoop），
    自动降级为线程池执行，保证跨平台兼容。

    Attributes:
        timeout: 命令执行超时秒数。
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

    def is_read_only(self, tool_input: dict[str, Any]) -> bool:
        """粗略判断是否只读：只有 ls/cat/echo/pwd/find/grep 类命令视为只读。"""
        command = tool_input.get("command", "").strip()
        read_only_prefixes = ("ls", "cat", "echo", "pwd", "find", "grep", "head", "tail", "wc")
        return command.split()[0] in read_only_prefixes if command else True

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """执行 shell 命令，逐行 yield 输出。

        优先使用异步子进程（真正流式）；若环境不支持则降级到线程池（一次性返回）。

        Args:
            tool_input: 包含 command（必填）和可选 timeout 的参数字典。
            context: 工具执行上下文（预留给权限扩展）。

        Yields:
            命令输出的文本片段。超时或异常时 yield 错误描述。
        """
        command = tool_input["command"]
        timeout = min(
            tool_input.get("timeout", self._timeout),
            _DEFAULT_TIMEOUT * 2,
        )

        try:
            # 优先路径：异步子进程，支持逐行流式输出
            # 需要 ProactorEventLoop（Windows）或任意 loop（Linux/macOS）
            process = await asyncio.create_subprocess_exec(
                *_SHELL,
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None

            async def _collect() -> list[str]:
                lines = []
                async for line in process.stdout:  # type: ignore[union-attr]
                    lines.append(line.decode(errors="replace"))
                await process.wait()
                return lines

            lines = await asyncio.wait_for(_collect(), timeout=timeout)
            for line in lines:
                yield line

            if process.returncode and process.returncode != 0:
                yield f"\n[退出码: {process.returncode}]\n"

        except NotImplementedError:
            # 降级路径：Windows SelectorEventLoop（如 Jupyter）不支持异步子进程
            # 用线程池执行同步 subprocess，命令完成后一次性返回全部输出
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
