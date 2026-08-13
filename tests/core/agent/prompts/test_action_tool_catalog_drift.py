"""The action prompt's prose tool catalog must agree with the tool registry.

``_SYSTEM_PROMPT_BASE`` describes ~19 tools in prose on the same call that
carries their JSON schemas. The prose is where the routing nuance lives ("use
``llm_set_provider`` ONLY when the user names an exact provider"), so it cannot
simply be deleted — but two descriptions of one tool drift, and the failure is
silent: the model is told to call a tool that no longer exists, or a new tool
ships with no routing guidance and is never selected.
"""

from __future__ import annotations

import re

from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE
from core.domain.types.tools import ToolSurface
from tools.registry import get_registered_tools

#: ``- tool_name — description`` entries under the prompt's "Other tools:" list.
_CATALOG_ENTRY = re.compile(r"^- ([a-z][a-z0-9_]+) —", re.MULTILINE)

#: Named in prose but not registered as ordinary action tools. Each is a real
#: capability the model reaches another way, so absence from the registry is
#: correct rather than drift.
_NOT_REGISTRY_TOOLS = frozenset(
    {
        # Structured handoff to the conversational assistant, handled by the
        # turn engine rather than dispatched as a registered tool.
        "assistant_handoff",
    }
)


def _prose_tool_names() -> frozenset[str]:
    start = _SYSTEM_PROMPT_BASE.find("Other tools:")
    assert start != -1, "the action prompt no longer has an 'Other tools:' catalog"
    return frozenset(_CATALOG_ENTRY.findall(_SYSTEM_PROMPT_BASE[start:]))


def _registered_action_tool_names() -> frozenset[str]:
    names: set[str] = set()
    for surface in (ToolSurface.ACTION, ToolSurface.CHAT, ToolSurface.INVESTIGATION):
        names.update(tool.name for tool in get_registered_tools(surface))
    return frozenset(names)


def test_every_tool_the_action_prompt_describes_still_exists() -> None:
    """A renamed or deleted tool leaves the prompt instructing a dead name."""
    # Arrange
    described = _prose_tool_names() - _NOT_REGISTRY_TOOLS

    # Act
    missing = sorted(described - _registered_action_tool_names())

    # Assert
    assert missing == [], (
        "the action prompt describes tools that are not registered on any action "
        f"surface: {missing}. Rename them in "
        "core/agent_harness/prompts/action/text.py, or add them to "
        "_NOT_REGISTRY_TOOLS when the model reaches them another way."
    )


def test_the_prose_catalog_is_not_the_only_record_of_a_tool() -> None:
    """Prose entries must carry routing nuance, not restate the JSON schema.

    Pins the reason the catalog is allowed to exist: an entry that adds no rule
    is pure duplication of the schema already on the call, and duplication is
    what drifts.
    """
    # Arrange
    start = _SYSTEM_PROMPT_BASE.find("Other tools:")
    catalog = _SYSTEM_PROMPT_BASE[start:]
    rule_markers = ("ONLY", "NEVER", "never", "Do NOT", "do NOT", "must", "MUST", "instead")

    # Act
    entries = [line for line in catalog.splitlines() if _CATALOG_ENTRY.match(line)]
    with_rules = [line for line in entries if any(m in line for m in rule_markers)]

    # Assert — a catalog of pure restatement should be deleted, not maintained.
    assert entries, "no catalog entries found"
    assert with_rules, (
        "no entry in the action prompt's tool catalog carries a routing rule; "
        "if the catalog only restates the JSON schemas, delete it rather than "
        "keeping two descriptions of every tool in sync."
    )
