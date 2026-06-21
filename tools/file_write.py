"""文件写入工具，创建新文件或覆盖已有文件的全部内容。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from tools.base import BaseTool, ToolUseContext


class FileWriteTool(BaseTool):
    """创建新文件或覆盖已有文件的全部内容。

    与 FileEditTool（str_replace 精确替换）的区别：
    FileWriteTool 写入整个文件，适合创建新文件或完全重写文件内容。
    FileEditTool 只替换文件中的某个片段，适合局部修改。
    """

    @property
    def name(self) -> str:
        return "Write"

    @property
    def description(self) -> str:
        return (
            "创建新文件或覆盖已有文件的全部内容。"
            "适合创建新文件或完全重写文件。"
            "如果只需修改文件的某一部分，请使用 Edit 工具。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件路径（绝对路径或相对路径）。",
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的完整内容。",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """写入文件内容，yield 操作结果描述。

        Args:
            tool_input: 包含 file_path 和 content。
            context: 工具执行上下文。

        Yields:
            操作成功的摘要，或失败原因。
        """
        file_path = Path(tool_input["file_path"]).expanduser()
        content: str = tool_input["content"]

        is_new = not file_path.exists()

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        except OSError as e:
            yield f"写入失败：{e}\n"
            return

        action = "已创建" if is_new else "已覆盖"
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        yield f"{action}文件：{file_path}（{lines} 行）\n"
