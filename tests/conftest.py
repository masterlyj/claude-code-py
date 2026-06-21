"""Pytest configuration and shared fixtures for the claude-code-py test suite.

Adds the project root to sys.path so all source packages (permissions/,
tools/) are importable without installing the project as a package.
Provides lightweight shared fixtures used across multiple test modules.
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap — must run before any project import
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

import pytest

from permissions.manager import PermissionContext, PermissionManager
from permissions.rules import PermissionMode
from tools.base import ToolUseContext


@pytest.fixture()
def bypass_ctx() -> ToolUseContext:
    """Return a ToolUseContext whose PermissionManager is in BYPASS mode.

    BYPASS mode approves every tool call without asking, making it the
    safest default for unit tests that only care about tool behaviour,
    not permission logic.
    """
    manager = PermissionManager(PermissionContext(mode=PermissionMode.BYPASS))
    return ToolUseContext(
        permission_manager=manager,
        model="test-model",
        tools=[],
        session_id="test-session",
    )
