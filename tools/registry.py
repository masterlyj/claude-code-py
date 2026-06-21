"""工具注册表，管理所有可用工具的注册与查找。

对应原版 src/tools.ts 中的 getTools() 和 findToolByName()。
目前只管理内置工具，MCP 工具在二期接入。

对外暴露：
  get_tools     — 返回当前启用的全部工具列表
  find_tool     — 按名称查找工具
"""

from __future__ import annotations

from tools.base import BaseTool
from tools.bash import BashTool
from tools.file_edit import FileEditTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def get_tools() -> list[BaseTool]:
    """返回默认启用的全部内置工具。

    Returns:
        工具实例列表，顺序决定了注入 API 时的 schema 顺序。
    """
    return [
        BashTool(),
        FileReadTool(),
        FileEditTool(),
        FileWriteTool(),
    ]


def find_tool(name: str, tools: list[BaseTool] | None = None) -> BaseTool | None:
    """按名称查找工具实例。

    Args:
        name: 工具名称，大小写敏感，对应各工具的 name 属性。
        tools: 在此列表中查找；为 None 时使用 get_tools() 默认列表。

    Returns:
        匹配的工具实例，未找到时返回 None。
    """
    pool = tools if tools is not None else get_tools()
    return next((t for t in pool if t.name == name), None)
