"""文件读取工具，读取本地文件内容并返回带行号的文本。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from tools.base import BaseTool, ToolUseContext

# 单次读取的最大字节数，防止超大文件撑爆上下文
_MAX_SIZE_BYTES = 256 * 1024  # 256 KB


class FileReadTool(BaseTool):
    """读取本地文件内容，返回带行号的文本。

    支持通过 start_line / end_line 读取指定行范围，
    适合大文件的分段读取场景。
    """

    @property
    def name(self) -> str:
        return "Read"

    @property
    def description(self) -> str:
        return (
            "读取本地文件的内容。返回带行号的文本，格式为「行号\\t内容」。"
            "可通过 start_line 和 end_line 读取指定行范围。"
            f"文件大小上限为 {_MAX_SIZE_BYTES // 1024} KB。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件的绝对路径或相对路径。",
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（从 1 开始），不填则从头读取。",
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（含），不填则读到末尾。",
                },
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """读取文件并 yield 带行号的内容。

        Args:
            tool_input: 包含 file_path，以及可选的 start_line / end_line。
            context: 工具执行上下文。

        Yields:
            带行号的文件内容文本，或错误描述。
        """
        file_path = Path(tool_input["file_path"]).expanduser()
        start_line: int | None = tool_input.get("start_line")
        end_line: int | None = tool_input.get("end_line")

        if not file_path.exists():
            yield f"文件不存在：{file_path}\n"
            return

        if not file_path.is_file():
            yield f"路径不是文件：{file_path}\n"
            return

        size = os.path.getsize(file_path)
        if size > _MAX_SIZE_BYTES:
            yield (
                f"文件过大（{size // 1024} KB），超过上限 {_MAX_SIZE_BYTES // 1024} KB。"
                f"请使用 start_line / end_line 分段读取。\n"
            )
            return

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            yield f"读取失败：{e}\n"
            return

        lines = content.splitlines(keepends=True)

        # 行范围处理（转为 0-based 索引）
        lo = (start_line - 1) if start_line is not None else 0
        hi = end_line if end_line is not None else len(lines)
        lo = max(0, lo)
        hi = min(len(lines), hi)

        if lo >= hi:
            yield f"指定行范围 [{start_line}, {end_line}] 无内容（文件共 {len(lines)} 行）。\n"
            return

        result_lines = []
        for i, line in enumerate(lines[lo:hi], start=lo + 1):
            result_lines.append(f"{i}\t{line}")

        yield "".join(result_lines)
