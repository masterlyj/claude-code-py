"""Tests for the tool registry.

Covers tools/registry.py:
  - get_tools() — returns exactly the four built-in tools
  - find_tool()  — locates tools by name, handles missing names, and accepts
                   a custom tool list
"""

from __future__ import annotations

import pytest

from tools.bash import BashTool
from tools.file_edit import FileEditTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.registry import find_tool, get_tools


# ---------------------------------------------------------------------------
# get_tools
# ---------------------------------------------------------------------------


def test_get_tools_returns_all_four():
    """get_tools() returns a list of exactly four tool instances."""
    tools = get_tools()

    assert len(tools) == 4


def test_get_tools_names():
    """get_tools() contains exactly the Bash, Read, Edit, and Write tools."""
    names = {t.name for t in get_tools()}

    assert names == {"Bash", "Read", "Edit", "Write"}


def test_get_tools_returns_correct_types():
    """Each tool in get_tools() is an instance of the expected concrete class."""
    tools_by_name = {t.name: t for t in get_tools()}

    assert isinstance(tools_by_name["Bash"], BashTool)
    assert isinstance(tools_by_name["Read"], FileReadTool)
    assert isinstance(tools_by_name["Edit"], FileEditTool)
    assert isinstance(tools_by_name["Write"], FileWriteTool)


# ---------------------------------------------------------------------------
# find_tool
# ---------------------------------------------------------------------------


def test_find_tool_found():
    """find_tool('Bash') returns a BashTool instance when using the default list."""
    result = find_tool("Bash")

    assert result is not None
    assert isinstance(result, BashTool)


def test_find_tool_not_found():
    """find_tool returns None for a name that matches no registered tool."""
    result = find_tool("NotExist")

    assert result is None


def test_find_tool_custom_list():
    """find_tool respects a caller-supplied tool list instead of the default."""
    custom_tools = [FileReadTool(), FileWriteTool()]

    # Should find Read in the custom list
    found = find_tool("Read", custom_tools)
    assert found is not None
    assert isinstance(found, FileReadTool)

    # Bash is NOT in the custom list — must return None
    missing = find_tool("Bash", custom_tools)
    assert missing is None


def test_find_tool_case_sensitive():
    """Tool name lookup is case-sensitive: 'bash' does not match 'Bash'."""
    result = find_tool("bash")

    assert result is None


def test_find_tool_returns_first_match_from_custom_list():
    """When multiple tools share a name in a custom list, the first is returned."""
    tool_a = FileReadTool()
    tool_b = FileReadTool()
    custom = [tool_a, tool_b]

    result = find_tool("Read", custom)

    assert result is tool_a
