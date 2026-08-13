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

#: Names described in prose but intentionally absent from the tool registry.
#: Empty on purpose — every catalog entry today is a registered tool, and an
#: exemption here silently removes that name from the drift check.
_NOT_REGISTRY_TOOLS: frozenset[str] = frozenset()

#: Entries whose prose carries routing nuance that exists nowhere else — the
#: reason the catalog is kept rather than deleted in favour of the JSON schemas.
#: Pinned individually: a catalog-wide "at least one" check stays satisfied by
#: unrelated entries while any single one quietly decays into restatement.
_ENTRIES_WITH_ROUTING_RULES = frozenset(
    {
        "code_implement",
        "investigation_start",
        "llm_set_provider",
        "memory_remember",
        "skill_view",
        "work_task_schedule_checkin",
    }
)

#: Words that mark a line as a rule rather than a description.
_RULE_MARKERS = ("ONLY", "NEVER", "never", "Do NOT", "do NOT", "must", "MUST", "instead", "not a")


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


def test_the_entries_that_carry_routing_rules_still_carry_them() -> None:
    """Prose entries must add rules the JSON schema cannot express.

    Only 6 of 19 entries carry a rule today, so requiring it of all of them
    would fail; requiring it of *any* of them is satisfied forever by the six.
    Each known rule-bearer is pinned instead, so one decaying into pure
    restatement is caught while the rest stay free to be plain descriptions.
    """
    # Arrange
    start = _SYSTEM_PROMPT_BASE.find("Other tools:")
    catalog = _SYSTEM_PROMPT_BASE[start:]
    bounds = [m.start() for m in _CATALOG_ENTRY.finditer(catalog)] + [len(catalog)]

    # Act
    rule_bearing = set()
    for index in range(len(bounds) - 1):
        block = catalog[bounds[index] : bounds[index + 1]]
        name = _CATALOG_ENTRY.match(block).group(1)
        if any(marker in block for marker in _RULE_MARKERS):
            rule_bearing.add(name)

    # Assert
    lost = sorted(_ENTRIES_WITH_ROUTING_RULES - rule_bearing)
    assert lost == [], (
        f"these catalog entries no longer carry a routing rule: {lost}. Either "
        "restore the guidance or drop the entry — an entry that only restates "
        "the JSON schema already on the call is duplication that will drift."
    )
