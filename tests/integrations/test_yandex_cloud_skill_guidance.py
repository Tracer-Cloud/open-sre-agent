"""The Yandex Cloud workflow guidance has to survive the registry's truncation.

The guidance is far longer than the 2400-char slice the registry attaches, so
what matters is not that it loads but *which part* of it reaches the model. The
paragraph that stops the agent hunting for Kubernetes pods in the Yandex Cloud
API is the load-bearing one; it sits inside the slice today and nothing else
pins it there.
"""

from __future__ import annotations

from pathlib import Path

from core.tool_framework.skill_guidance import (
    format_tool_skill_guidance,
    load_tool_skill_guidance,
)
from tools.registry import clear_tool_registry_cache, get_registered_tool_map

SKILL_FILE = (
    Path(__file__).resolve().parents[2] / "integrations" / "yandex_cloud" / "tools" / "SKILL.md"
)
#: The ceiling the registry applies to attached guidance.
MAX_ATTACHED_CHARS = 2400
GUIDED_TOOLS = frozenset({"find_yc_api", "execute_yc_operation"})
#: Tools named in the guidance that this tree does not ship would hand the model
#: a next action it cannot take.
LATER_FAMILY_TOOLS = (
    "read_yc_audit_events",
    "list_yc_k8s_clusters",
    "get_yc_k8s_cluster",
)
#: Tools the guidance may name because this tree does ship them. Kept explicit
#: so that adding a family means moving a name across rather than deleting one.
SHIPPED_FAMILY_TOOLS = (
    "list_yc_db_clusters",
    "get_yc_db_cluster",
    "read_yc_db_logs",
)


class TestTheSkillDocument:
    def test_it_loads_without_diagnostics(self) -> None:
        result = load_tool_skill_guidance(SKILL_FILE, known_tool_names=GUIDED_TOOLS)

        assert result.diagnostics == [], f"Unexpected diagnostics: {result.diagnostics}"
        assert result.skill is not None

    def test_a_past_incident_names_the_window_each_reader_accepts(self) -> None:
        """query_yc_metrics and read_yc_logs do not share parameter names."""
        text = SKILL_FILE.read_text(encoding="utf-8")

        assert "`from_time`" in text and "`to_time`" in text
        assert "`since`" in text and "`until`" in text

    def test_it_declares_exactly_the_two_generic_readers(self) -> None:
        """A name here that no tool answers to attaches the guidance to nothing."""
        result = load_tool_skill_guidance(SKILL_FILE, known_tool_names=GUIDED_TOOLS)

        assert result.skill is not None
        assert set(result.skill.tool_names) == GUIDED_TOOLS


class TestWhatReachesTheModel:
    def test_both_generic_readers_carry_the_guidance(self) -> None:
        clear_tool_registry_cache()
        tools = get_registered_tool_map("action")

        for name in sorted(GUIDED_TOOLS):
            assert tools[name].skill_guidance, f"{name} carries no guidance"

    def test_the_pod_paragraph_survives_truncation(self) -> None:
        """Without it the agent burns the investigation looking for pods in the REST API."""
        clear_tool_registry_cache()
        tools = get_registered_tool_map("action")

        for name in sorted(GUIDED_TOOLS):
            attached = tools[name].skill_guidance
            assert "A pod is not a Yandex Cloud resource" in attached, name

    def test_it_names_no_tool_this_tree_does_not_ship(self) -> None:
        """Later families are described, but never as an action to take now."""
        result = load_tool_skill_guidance(SKILL_FILE, known_tool_names=GUIDED_TOOLS)

        assert result.skill is not None
        for absent in LATER_FAMILY_TOOLS:
            assert absent not in result.skill.content, absent

    def test_a_shipped_family_is_named_as_the_way_to_reach_it(self) -> None:
        """The other half of the rule above: a tool that exists should be offered.

        Leaving "not readable yet" in place after a family lands is the same
        failure as naming a tool that does not exist - the model is told to fall
        back when it could have read the thing.
        """
        result = load_tool_skill_guidance(SKILL_FILE, known_tool_names=GUIDED_TOOLS)

        assert result.skill is not None
        registered = get_registered_tool_map("action")
        for shipped in SHIPPED_FAMILY_TOOLS:
            assert shipped in registered, shipped
            assert shipped in result.skill.content, shipped

    def test_the_attached_slice_stays_within_the_registry_ceiling(self) -> None:
        clear_tool_registry_cache()
        tools = get_registered_tool_map("action")

        for name in sorted(GUIDED_TOOLS):
            assert len(tools[name].skill_guidance) <= MAX_ATTACHED_CHARS, name

    def test_the_document_is_longer_than_the_slice(self) -> None:
        """If it ever fits whole, the truncation assertions above stop meaning anything."""
        result = load_tool_skill_guidance(SKILL_FILE, known_tool_names=GUIDED_TOOLS)

        assert result.skill is not None
        assert len(format_tool_skill_guidance(result.skill)) > MAX_ATTACHED_CHARS
