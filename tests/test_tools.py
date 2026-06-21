"""Tests for the individual tool implementations.

Covers:
  - tools/bash.py        — BashTool schema
  - tools/file_read.py   — FileReadTool schema + execute behaviour
  - tools/file_edit.py   — FileEditTool schema + execute behaviour
  - tools/file_write.py  — FileWriteTool schema + execute behaviour

All file tests use pytest's tmp_path fixture so they create no lasting
artefacts on disk.  The bypass_ctx fixture from conftest.py is used to
satisfy the ToolUseContext parameter; permission logic is not under test
here.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from tools.bash import BashTool
from tools.file_edit import FileEditTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


async def collect(gen: AsyncIterator[str]) -> str:
    """Drain an async generator and join all yielded strings into one.

    Args:
        gen: Async generator that yields str chunks.

    Returns:
        Concatenated output of every yielded chunk.
    """
    chunks: list[str] = []
    async for chunk in gen:
        chunks.append(chunk)
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Schema tests — no I/O required
# ---------------------------------------------------------------------------


def test_bash_tool_schema():
    """BashTool exposes name 'Bash' and requires a 'command' input field."""
    tool = BashTool()

    assert tool.name == "Bash"
    assert "command" in tool.input_schema["properties"]
    assert "command" in tool.input_schema["required"]


def test_file_read_tool_schema():
    """FileReadTool exposes name 'Read' and requires a 'file_path' input field."""
    tool = FileReadTool()

    assert tool.name == "Read"
    assert "file_path" in tool.input_schema["properties"]
    assert "file_path" in tool.input_schema["required"]


def test_file_edit_tool_schema():
    """FileEditTool exposes name 'Edit' and requires file_path/old_string/new_string."""
    tool = FileEditTool()

    assert tool.name == "Edit"
    required = tool.input_schema["required"]
    assert "file_path" in required
    assert "old_string" in required
    assert "new_string" in required


def test_file_write_tool_schema():
    """FileWriteTool exposes name 'Write' and requires file_path and content."""
    tool = FileWriteTool()

    assert tool.name == "Write"
    required = tool.input_schema["required"]
    assert "file_path" in required
    assert "content" in required


# ---------------------------------------------------------------------------
# FileReadTool — execute
# ---------------------------------------------------------------------------


async def test_file_read_existing_file(tmp_path: Path, bypass_ctx):
    """Reading an existing file yields numbered lines in '<n>\\t<text>' format."""
    sample = tmp_path / "hello.txt"
    sample.write_text("line one\nline two\nline three\n", encoding="utf-8")

    tool = FileReadTool()
    output = await collect(tool.execute({"file_path": str(sample)}, bypass_ctx))

    assert "1\tline one\n" in output
    assert "2\tline two\n" in output
    assert "3\tline three\n" in output


async def test_file_read_line_range(tmp_path: Path, bypass_ctx):
    """Using start_line/end_line returns only the requested line range."""
    sample = tmp_path / "numbered.txt"
    sample.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    tool = FileReadTool()
    output = await collect(
        tool.execute(
            {"file_path": str(sample), "start_line": 2, "end_line": 4},
            bypass_ctx,
        )
    )

    # Lines 2–4 present, lines 1 and 5 absent
    assert "2\tb\n" in output
    assert "3\tc\n" in output
    assert "4\td\n" in output
    assert "1\ta\n" not in output
    assert "5\te\n" not in output


async def test_file_read_not_found(tmp_path: Path, bypass_ctx):
    """Reading a non-existent path yields a message containing '不存在'."""
    tool = FileReadTool()
    output = await collect(
        tool.execute({"file_path": str(tmp_path / "ghost.txt")}, bypass_ctx)
    )

    assert "不存在" in output


async def test_file_read_line_range_out_of_bounds(tmp_path: Path, bypass_ctx):
    """Requesting a line range beyond the file length yields a diagnostic message."""
    sample = tmp_path / "short.txt"
    sample.write_text("only one line\n", encoding="utf-8")

    tool = FileReadTool()
    output = await collect(
        tool.execute(
            {"file_path": str(sample), "start_line": 100, "end_line": 200},
            bypass_ctx,
        )
    )

    # Should explain that the range produced no content
    assert "无内容" in output or "行范围" in output


# ---------------------------------------------------------------------------
# FileWriteTool — execute
# ---------------------------------------------------------------------------


async def test_file_write_creates_new_file(tmp_path: Path, bypass_ctx):
    """Writing to a new path creates the file with the given content."""
    dest = tmp_path / "new_file.txt"
    tool = FileWriteTool()

    output = await collect(
        tool.execute({"file_path": str(dest), "content": "hello world\n"}, bypass_ctx)
    )

    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "hello world\n"
    assert "已创建" in output


async def test_file_write_overwrites_existing(tmp_path: Path, bypass_ctx):
    """Writing to an existing path replaces its content and reports '已覆盖'."""
    dest = tmp_path / "existing.txt"
    dest.write_text("old content\n", encoding="utf-8")

    tool = FileWriteTool()
    output = await collect(
        tool.execute({"file_path": str(dest), "content": "new content\n"}, bypass_ctx)
    )

    assert dest.read_text(encoding="utf-8") == "new content\n"
    assert "已覆盖" in output


# ---------------------------------------------------------------------------
# FileEditTool — execute
# ---------------------------------------------------------------------------


async def test_file_edit_replace_success(tmp_path: Path, bypass_ctx):
    """Replacing a unique string succeeds and the file reflects the change."""
    target = tmp_path / "source.py"
    target.write_text("def foo():\n    pass\n", encoding="utf-8")

    tool = FileEditTool()
    output = await collect(
        tool.execute(
            {
                "file_path": str(target),
                "old_string": "    pass",
                "new_string": "    return 42",
            },
            bypass_ctx,
        )
    )

    assert "已编辑" in output
    assert "return 42" in target.read_text(encoding="utf-8")


async def test_file_edit_not_found_string(tmp_path: Path, bypass_ctx):
    """Attempting to replace a string absent from the file yields an error."""
    target = tmp_path / "code.txt"
    target.write_text("alpha beta\n", encoding="utf-8")

    tool = FileEditTool()
    output = await collect(
        tool.execute(
            {
                "file_path": str(target),
                "old_string": "gamma",
                "new_string": "delta",
            },
            bypass_ctx,
        )
    )

    assert "未找到" in output
    # File must be unchanged
    assert target.read_text(encoding="utf-8") == "alpha beta\n"


async def test_file_edit_ambiguous_match(tmp_path: Path, bypass_ctx):
    """Multiple matches without replace_all=True yields an error about count."""
    target = tmp_path / "repeat.txt"
    target.write_text("foo\nfoo\nfoo\n", encoding="utf-8")

    tool = FileEditTool()
    output = await collect(
        tool.execute(
            {
                "file_path": str(target),
                "old_string": "foo",
                "new_string": "bar",
            },
            bypass_ctx,
        )
    )

    assert "3" in output  # reports match count
    # File must be unchanged
    assert target.read_text(encoding="utf-8") == "foo\nfoo\nfoo\n"


async def test_file_edit_replace_all(tmp_path: Path, bypass_ctx):
    """replace_all=True replaces every occurrence of old_string."""
    target = tmp_path / "multi.txt"
    target.write_text("x x x\n", encoding="utf-8")

    tool = FileEditTool()
    output = await collect(
        tool.execute(
            {
                "file_path": str(target),
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            },
            bypass_ctx,
        )
    )

    assert "已编辑" in output
    assert target.read_text(encoding="utf-8") == "y y y\n"
    # Should report 3 replacements
    assert "3" in output


async def test_file_edit_create_via_empty_old_string(tmp_path: Path, bypass_ctx):
    """Passing old_string='' creates or overwrites the file with new_string."""
    dest = tmp_path / "brand_new.txt"
    tool = FileEditTool()

    output = await collect(
        tool.execute(
            {
                "file_path": str(dest),
                "old_string": "",
                "new_string": "created by edit\n",
            },
            bypass_ctx,
        )
    )

    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "created by edit\n"
    assert "已创建" in output
