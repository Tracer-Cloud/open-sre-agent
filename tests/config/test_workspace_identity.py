"""Workspace identity and capability-warning runtime facts."""

from __future__ import annotations

import pytest

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


def test_capability_warnings_report_no_shell_when_bash_and_sh_are_both_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a PATH with neither shell. The agent is told it can run shell
    # commands, so this gap has to reach the boot warnings rather than surface
    # as a failed command mid-turn.
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    tools = {"curl": "/usr/bin/curl", "bash": "", "sh": ""}

    # Act
    facts = capability_warning_facts(tools)

    # Assert
    assert facts["shell_available"] is False
    assert "no interactive shell (bash/sh) on PATH" in facts["capability_warnings"]
    assert not any("curl" in warning for warning in facts["capability_warnings"])


def test_capability_warnings_accept_sh_as_the_shell_when_bash_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: bash absent but sh present. Minimal containers ship only sh, and
    # warning about it would train operators to ignore the warnings.
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    tools = {"curl": "/usr/bin/curl", "bash": "", "sh": "/bin/sh"}

    # Act
    facts = capability_warning_facts(tools)

    # Assert
    assert facts["shell_available"] is True
    assert not any("shell" in warning for warning in facts["capability_warnings"])


def test_capability_warnings_drop_the_network_line_when_egress_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the network warning states the sandbox default, so it must go
    # when the operator has opted out of that default.
    monkeypatch.setenv(OPENSRE_ALLOW_NETWORK_ENV, "1")
    tools = {"curl": "/usr/bin/curl", "bash": "/bin/bash", "sh": "/bin/sh"}

    # Act
    facts = capability_warning_facts(tools)

    # Assert
    assert facts["network_egress"] is True
    assert facts["capability_warnings"] == []


def test_capability_warnings_probe_path_only_when_no_tools_are_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the caller passes nothing, so the probe resolves PATH itself.
    # Substituting installed_tools keeps that branch under test without making
    # the assertion depend on what happens to be installed on the test machine.
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    calls: list[int] = []

    def _fake_installed_tools() -> dict[str, str]:
        calls.append(1)
        return {"curl": "", "bash": "", "sh": ""}

    monkeypatch.setattr("config.runtime_metadata.probes.installed_tools", _fake_installed_tools)

    # Act
    facts = capability_warning_facts()

    # Assert
    assert calls == [1]
    assert facts["capability_warnings"] == [
        "curl is not on PATH",
        "no interactive shell (bash/sh) on PATH",
        "network egress is blocked for sandboxed code by default",
    ]


def test_capability_warnings_treat_an_empty_mapping_as_an_answer_not_a_missing_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: an empty dict is falsy but is still a caller-supplied result. A
    # `tools or installed_tools()` check would silently fall back to walking the
    # real PATH here, which is exactly the live-PATH flakiness to keep out.
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)

    def _fail_if_called() -> dict[str, str]:
        raise AssertionError("PATH must not be probed when the caller supplied tools")

    monkeypatch.setattr("config.runtime_metadata.probes.installed_tools", _fail_if_called)

    # Act
    facts = capability_warning_facts({})

    # Assert
    assert facts["shell_available"] is False
    assert facts["capability_warnings"] == [
        "no interactive shell (bash/sh) on PATH",
        "network egress is blocked for sandboxed code by default",
    ]


def test_capability_warnings_line_in_prompt() -> None:
    block = render_static_runtime_facts(
        {
            "opensre_version": "0.1",
            "capability_warnings": ["curl is not on PATH", "network egress is blocked"],
        }
    )
    assert "capability warnings at boot: curl is not on PATH; network egress is blocked" in block


class TestRepoIdentityHostIsExact:
    """Only a real GitHub host may yield a bare ``owner/repo`` identity.

    Substring matching on "github.com" accepts any URL that merely contains it,
    so a remote pointing at an attacker host resolves to a clean-looking
    identity. That fact reaches prompts and reports, where it reads as the
    project's real repository.
    """

    def test_genuine_github_remotes_resolve(self) -> None:
        from config.runtime_metadata.probes import _normalize_repo_identity

        for url in (
            "https://github.com/Tracer-Cloud/opensre",
            "https://github.com/Tracer-Cloud/opensre.git",
            "git@github.com:Tracer-Cloud/opensre.git",
            "ssh://git@github.com/Tracer-Cloud/opensre",
        ):
            assert _normalize_repo_identity(url) == "Tracer-Cloud/opensre", url

    def test_host_containing_github_com_is_not_treated_as_github(self) -> None:
        from config.runtime_metadata.probes import _normalize_repo_identity

        # A planted identity that must never be reported as owner/repo.
        for url in (
            "https://evil.test/github.com/attacker/repo",
            "git@notgithub.com:attacker/repo",
            "https://github.com.attacker.test/attacker/repo",
        ):
            assert _normalize_repo_identity(url) != "attacker/repo", url

    def test_non_github_remotes_are_left_alone(self) -> None:
        from config.runtime_metadata.probes import _normalize_repo_identity

        url = "https://gitlab.com/org/repo"
        assert _normalize_repo_identity(url) == url
