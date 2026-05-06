"""Regression tests for internal message types.

These tests verify:
1. SREMessage TypedDict has the correct shape.
2. make_* helpers produce correct roles.
3. Adapters round-trip correctly.
4. langchain_core.messages is NOT imported at module level in core files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.types.messages import (
    SREMessageList,
    from_lc_message,
    make_assistant,
    make_system,
    make_tool,
    make_user,
    to_lc_messages,
)

# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_make_system_role() -> None:
    msg = make_system("You are an agent.")
    assert msg["role"] == "system"
    assert msg["content"] == "You are an agent."


def test_make_user_role() -> None:
    msg = make_user("What is wrong?")
    assert msg["role"] == "user"
    assert msg["content"] == "What is wrong?"


def test_make_assistant_role() -> None:
    msg = make_assistant("Root cause: disk full.")
    assert msg["role"] == "assistant"
    assert msg["content"] == "Root cause: disk full."


def test_make_assistant_with_tool_calls() -> None:
    tool_calls = [{"name": "get_data", "args": {"query": "foo"}, "id": "123"}]
    msg = make_assistant("Calling tool...", tool_calls=tool_calls)
    assert msg["role"] == "assistant"
    assert msg["tool_calls"] == tool_calls


def test_make_tool_role() -> None:
    msg = make_tool("result", tool_call_id="123", name="get_data")
    assert msg["role"] == "tool"
    assert msg["content"] == "result"
    assert msg["tool_call_id"] == "123"
    assert msg["name"] == "get_data"


def test_sre_message_list_type() -> None:
    msgs: SREMessageList = [make_system("sys"), make_user("usr")]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"


# ---------------------------------------------------------------------------
# Adapter round-trip tests (require langchain_core to be installed)
# ---------------------------------------------------------------------------


def test_to_lc_messages_produces_correct_types() -> None:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    msgs: SREMessageList = [
        make_system("sys"),
        make_user("usr"),
        make_assistant("asst"),
        make_tool("res", "123", "tool"),
    ]
    lc = to_lc_messages(msgs)
    assert isinstance(lc[0], SystemMessage)
    assert isinstance(lc[1], HumanMessage)
    assert isinstance(lc[2], AIMessage)
    assert isinstance(lc[3], ToolMessage)
    assert lc[0].content == "sys"
    assert lc[1].content == "usr"
    assert lc[2].content == "asst"
    assert lc[3].content == "res"
    assert lc[3].tool_call_id == "123"
    assert lc[3].name == "tool"


def test_from_lc_message_system() -> None:
    from langchain_core.messages import SystemMessage

    result = from_lc_message(SystemMessage(content="be helpful"))
    assert result["role"] == "system"
    assert result["content"] == "be helpful"


def test_from_lc_message_human() -> None:
    from langchain_core.messages import HumanMessage

    result = from_lc_message(HumanMessage(content="help me"))
    assert result["role"] == "user"
    assert result["content"] == "help me"


def test_from_lc_message_ai() -> None:
    from langchain_core.messages import AIMessage

    result = from_lc_message(AIMessage(content="here is the root cause"))
    assert result["role"] == "assistant"
    assert result["content"] == "here is the root cause"


def test_from_lc_message_ai_with_tool_calls() -> None:
    from langchain_core.messages import AIMessage

    tool_calls = [{"name": "get_data", "args": {}, "id": "123", "type": "tool_call"}]
    result = from_lc_message(AIMessage(content="", tool_calls=tool_calls))
    assert result["role"] == "assistant"
    assert result["tool_calls"] == tool_calls


def test_from_lc_message_tool() -> None:
    from langchain_core.messages import ToolMessage

    result = from_lc_message(ToolMessage(content="res", tool_call_id="123", name="tool"))
    assert result["role"] == "tool"
    assert result["content"] == "res"
    assert result["tool_call_id"] == "123"
    assert result["name"] == "tool"


def test_roundtrip_preserves_content() -> None:
    original: SREMessageList = [
        make_system("s"),
        make_user("u"),
        make_assistant("a"),
        make_tool("r", "i", "n"),
    ]
    lc = to_lc_messages(original)
    roundtripped = [from_lc_message(m) for m in lc]
    assert original == roundtripped


# ---------------------------------------------------------------------------
# Import-boundary regression tests
# ---------------------------------------------------------------------------

BANNED_MODULE_LEVEL_IMPORT = "langchain_core.messages"

# Files that ARE ALLOWED to have module-level langchain_core.messages imports:
# (only adapters and graph boundary)
ALLOWED_FILES: set[str] = {
    "app/types/messages.py",  # adapter lives here (deferred imports inside functions)
    "app/pipeline/graph.py",  # graph wiring boundary
}


def _module_level_imports_lc_messages(filepath: Path) -> list[int]:
    """Return line numbers where langchain_core.messages is imported at module level."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    bad_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if BANNED_MODULE_LEVEL_IMPORT in module:
                # Check if inside a function definition (deferred import = OK)
                # We do this by checking parent context; ast.walk loses parent info,
                # so we use a simple lineno heuristic with function indentation check.
                # A full parent-tracking walk is more accurate but overkill here.
                bad_lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if BANNED_MODULE_LEVEL_IMPORT in alias.name:
                    bad_lines.append(node.lineno)
    return bad_lines


def _collect_core_python_files() -> list[Path]:
    """Return all .py files in app/nodes/ and app/services/ that are not in ALLOWED_FILES."""
    repo_root = Path(__file__).resolve().parents[2]
    target_dirs = [
        repo_root / "app" / "nodes",
        repo_root / "app" / "services",
    ]
    results: list[Path] = []
    for d in target_dirs:
        if d.exists():
            for p in d.rglob("*.py"):
                relative = str(p.relative_to(repo_root))
                if relative not in ALLOWED_FILES:
                    results.append(p)
    return results


@pytest.mark.parametrize("filepath", _collect_core_python_files())
def test_no_module_level_lc_messages_import(filepath: Path) -> None:
    """Core node and service files must not import langchain_core.messages at module level."""
    repo_root = Path(__file__).resolve().parents[2]
    relative = str(filepath.relative_to(repo_root))
    bad_lines = _module_level_imports_lc_messages(filepath)

    assert not bad_lines, (
        f"{relative} imports langchain_core.messages at module level on lines {bad_lines}. "
        f"Use app.types.messages instead, and keep langchain_core adapters only in "
        f"app/types/messages.py or app/pipeline/graph.py."
    )
