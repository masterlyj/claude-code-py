"""文件编辑工具，通过精确字符串替换修改文件内容。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from tools.base import BaseTool, ToolUseContext


class FileEditTool(BaseTool):
    """通过 str_replace 方式精确修改文件内容。

    要求提供待替换的原始文本（old_string）和替换后的文本（new_string）。
    old_string 必须在文件中唯一存在（默认），否则报错，
    避免模型在错误位置修改文件。

    支持 replace_all=True 替换所有匹配项，适合批量重命名场景。
    old_string 为空字符串时，将 new_string 写入（创建或覆盖）整个文件。
    """

    @property
    def name(self) -> str:
        return "Edit"

    @property
    def description(self) -> str:
        return (
            "精确替换文件中的指定文本。"
            "old_string 必须与文件中的内容完全匹配（含空格和缩进）。"
            "old_string 为空时，将 new_string 写入整个文件（创建或覆盖）。"
            "默认只替换第一处匹配；replace_all=true 时替换全部匹配项。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要编辑的文件路径（绝对路径或相对路径）。",
                },
                "old_string": {
                    "type": "string",
                    "description": "要被替换的原始文本。空字符串表示创建/覆盖整个文件。",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新文本。",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换所有匹配项，默认 false（只替换第一处）。",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """执行文件编辑，yield 操作结果描述。

        Args:
            tool_input: 包含 file_path、old_string、new_string 和可选 replace_all。
            context: 工具执行上下文。

        Yields:
            操作成功的摘要，或失败原因。
        """
        file_path = Path(tool_input["file_path"]).expanduser()
        old_string: str = tool_input["old_string"]
        new_string: str = tool_input["new_string"]
        replace_all: bool = tool_input.get("replace_all", False)

        # old_string 为空：创建或覆盖整个文件
        if old_string == "":
            is_new = not file_path.exists()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(new_string, encoding="utf-8")
            action = "已创建" if is_new else "已覆盖"
            yield f"{action}文件：{file_path}\n"
            return

        if not file_path.exists():
            yield f"文件不存在：{file_path}\n"
            return

        try:
            original = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            yield f"读取文件失败：{e}\n"
            return

        count = original.count(old_string)

        if count == 0:
            yield (
                f"未找到指定文本，编辑失败。\n"
                f"请确认 old_string 与文件内容完全一致（含空格、缩进、换行）。\n"
            )
            return

        # 非 replace_all 时要求唯一匹配，防止在错误位置修改
        if count > 1 and not replace_all:
            yield (
                f"找到 {count} 处匹配，编辑失败。\n"
                f"old_string 必须在文件中唯一，或设置 replace_all=true。\n"
            )
            return

        if replace_all:
            updated = original.replace(old_string, new_string)
            replaced_count = count
        else:
            updated = original.replace(old_string, new_string, 1)
            replaced_count = 1

        try:
            file_path.write_text(updated, encoding="utf-8")
        except OSError as e:
            yield f"写入文件失败：{e}\n"
            return

        yield f"已编辑 {file_path}，替换了 {replaced_count} 处匹配。\n"
