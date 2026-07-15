"""Tests for architecture action tools (clone, cleanup, save observations)."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace

from tests.tools.conftest import BaseToolContract
from tools import registry as registry_module
from tools.architecture_issue_tool.report_persistence import (
    ReportPersistenceError,
    sanitize_repo_name,
    save_architecture_observations,
)
from tools.architecture_issue_tool.tool import (
    architecture_cleanup_repo,
    architecture_clone_repo,
    architecture_save_observations,
)


class TestArchitectureCloneRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_clone_repo.__opensre_registered_tool__


class TestArchitectureCleanupRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_cleanup_repo.__opensre_registered_tool__


class TestArchitectureSaveObservationsContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_save_observations.__opensre_registered_tool__


def test_architecture_clone_and_cleanup_are_mutating() -> None:
    assert architecture_clone_repo.__opensre_registered_tool__.side_effect_level == "mutating"
    assert architecture_cleanup_repo.__opensre_registered_tool__.side_effect_level == "mutating"


def test_architecture_tools_are_action_surface_only() -> None:
    registry_module.clear_tool_registry_cache()
    action = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("action")
    }
    chat = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("chat")}

    for name in (
        "architecture_clone_repo",
        "architecture_cleanup_repo",
        "architecture_save_observations",
    ):
        assert name in action
        assert name not in chat

    assert "scan_architecture_imports" not in action
    assert "scan_module_placement" not in action
    assert "find_architecture_violations" not in action
    assert "find_architecture_violations" not in chat


def test_architecture_clone_repo_local_path(tmp_path: Path) -> None:
    result = architecture_clone_repo(
        owner="org",
        repo="repo",
        local_path=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["workspace_root"] == str(tmp_path.resolve())


def test_architecture_cleanup_refuses_outside_path(tmp_path: Path) -> None:
    result = architecture_cleanup_repo(workspace_root=str(tmp_path))
    assert result["ok"] is False
    assert "outside" in result["error"]


def test_sanitize_repo_name() -> None:
    assert sanitize_repo_name("Tracer-Cloud/opensre") == "Tracer-Cloud-opensre"
    assert sanitize_repo_name("  ") == "repo"


def test_save_architecture_observations_writes_markdown(tmp_path: Path) -> None:
    path = save_architecture_observations(
        session_id="sess-123",
        repo_name="opensre",
        observations="- import: core -> surfaces\n- size: big.py (900)",
        audit_id="abcd1234",
        home_dir=tmp_path,
    )
    assert path == tmp_path / "sess-123" / "opensre-architecture-audit-abcd1234.md"
    text = path.read_text(encoding="utf-8")
    assert "# Architecture audit observations" in text
    assert "sess-123" in text
    assert "- import: core -> surfaces" in text
    assert "- size: big.py (900)" in text


def test_save_architecture_observations_rejects_empty(tmp_path: Path) -> None:
    try:
        save_architecture_observations(
            session_id="sess-123",
            repo_name="opensre",
            observations="   ",
            home_dir=tmp_path,
        )
    except ReportPersistenceError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("expected ReportPersistenceError")


def test_architecture_save_observations_tool_uses_explicit_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "tools.architecture_issue_tool.tool.save_architecture_observations",
        partial(save_architecture_observations, home_dir=tmp_path),
    )
    result = architecture_save_observations(
        repo_name="opensre",
        observations="- placement: tools under wrong tree",
        session_id="ae4c7934-747e-4ffc-9f62-3143cd1ad5af",
    )
    assert result["ok"] is True
    saved = Path(result["path"])
    assert saved.exists()
    assert saved.parent.name == "ae4c7934-747e-4ffc-9f62-3143cd1ad5af"
    assert saved.name.startswith("opensre-architecture-audit-")
    assert saved.suffix == ".md"


def test_architecture_save_observations_tool_reads_session_from_context(
    tmp_path: Path, monkeypatch
) -> None:
    from core.agent_harness.tools.tool_context import (
        ACTION_TOOL_CONTEXT_RESOURCE_KEY,
        ActionToolContext,
    )
    from core.types import AgentToolContext

    monkeypatch.setattr(
        "tools.architecture_issue_tool.tool.save_architecture_observations",
        partial(save_architecture_observations, home_dir=tmp_path),
    )
    session = SimpleNamespace(session_id="ctx-session-id")
    context = AgentToolContext(
        resolved_integrations={},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolContext(
                session=session,
                console=SimpleNamespace(),
            )
        },
    )
    result = architecture_save_observations(
        repo_name="envoy",
        observations="- size: abi.h (14255)",
        context=context,
    )
    assert result["ok"] is True
    assert result["session_id"] == "ctx-session-id"
    assert Path(result["path"]).exists()
