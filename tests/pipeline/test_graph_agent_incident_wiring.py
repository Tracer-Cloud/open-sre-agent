"""Graph wiring for the ``agent_incident`` entry node."""

from __future__ import annotations

from app.pipeline.graph import build_graph


def _builder():
    return build_graph().builder


def _branch_ends(source: str, branch_name: str) -> dict[str, str] | None:
    branches = _builder().branches.get(source, {})
    branch = branches.get(branch_name)
    return getattr(branch, "ends", None) if branch is not None else None


def _nodes() -> set[str]:
    return set(_builder().nodes.keys())


def test_agent_incident_node_is_registered() -> None:
    assert "agent_incident" in _nodes()


def test_inject_auth_routes_agent_incident_mode() -> None:
    ends = _branch_ends("inject_auth", "route_by_mode")
    assert ends is not None
    assert ends.get("chat") == "router"
    assert ends.get("investigation") == "extract_alert"
    assert ends.get("agent_incident") == "agent_incident"


def test_agent_incident_shares_route_after_extract() -> None:
    for source in ("extract_alert", "agent_incident"):
        ends = _branch_ends(source, "route_after_extract")
        assert ends is not None, f"{source} must use route_after_extract"
        assert ends.get("end") == "__end__" or ends.get("end") == "__END__"  # LangGraph internal
        assert ends.get("investigate") == "resolve_integrations"
