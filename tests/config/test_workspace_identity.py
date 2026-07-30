"""Workspace identity and capability-warning runtime facts."""

from __future__ import annotations

from config.constants.runtime_metadata import (
    OPENSRE_ALLOW_NETWORK_ENV,
    OPENSRE_WORKSPACE_REPO_ENV,
)
from config.runtime_metadata import build_runtime_metadata
from config.runtime_metadata.probes import (
    capability_warning_facts,
    workspace_identity_facts,
)
from core.agent_harness.prompts.runtime_facts import render_static_runtime_facts


def test_workspace_repo_from_env(monkeypatch) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "Tracer-Cloud/opensre")
    assert workspace_identity_facts()["workspace_repo"] == "Tracer-Cloud/opensre"


def test_workspace_repo_normalizes_github_urls(monkeypatch) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "git@github.com:Tracer-Cloud/opensre.git")
    assert workspace_identity_facts()["workspace_repo"] == "Tracer-Cloud/opensre"
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "https://github.com/acme/widgets.git")
    assert workspace_identity_facts()["workspace_repo"] == "acme/widgets"


def test_read_git_origin_identity_from_config_body() -> None:
    """Parser is pure (no ``.git`` writes) so sandboxes and CI can exercise it."""
    from config.runtime_metadata.probes import read_git_origin_identity

    assert (
        read_git_origin_identity('[remote "origin"]\n\turl = git@github.com:acme/from-git.git\n')
        == "acme/from-git"
    )
    assert read_git_origin_identity("[core]\n\trepositoryformatversion = 0\n") == ""


def test_workspace_line_in_prompt_when_set(monkeypatch) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "acme/widgets")
    facts = build_runtime_metadata()
    block = render_static_runtime_facts(facts)
    assert "this OpenSRE workspace repo is acme/widgets" in block
    assert "treat “our” / “this repo” as acme/widgets" in block


def test_workspace_absence_line_when_unknown(monkeypatch) -> None:
    block = render_static_runtime_facts({"opensre_version": "0.1", "workspace_repo": ""})
    assert "no workspace git/GitHub repo was detected" in block


def test_capability_warnings_include_network_default(monkeypatch) -> None:
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    facts = capability_warning_facts({"curl": "", "bash": "/bin/bash", "sh": ""})
    assert facts["network_egress"] is False
    assert facts["shell_available"] is True
    assert any("curl" in w for w in facts["capability_warnings"])
    assert any("network egress" in w for w in facts["capability_warnings"])


def test_capability_warnings_line_in_prompt() -> None:
    block = render_static_runtime_facts(
        {
            "opensre_version": "0.1",
            "capability_warnings": ["curl is not on PATH", "network egress is blocked"],
        }
    )
    assert "capability warnings at boot: curl is not on PATH; network egress is blocked" in block
