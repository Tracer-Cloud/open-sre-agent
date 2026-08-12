"""Capability warning facts when PATH tools are missing.

Every case drives ``shutil.which`` or passes an explicit tool map, so no
assertion depends on what happens to be installed on the machine running the
suite — a host without ``curl`` and a host with it produce the same result.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest

from config.constants.runtime_metadata import OPENSRE_ALLOW_NETWORK_ENV
from config.runtime_metadata.probes import capability_warning_facts, installed_tools

_CURL_WARNING = "curl is not on PATH"
_NO_SHELL_WARNING = "no interactive shell (bash/sh) on PATH"
_NETWORK_WARNING = "network egress is blocked for sandboxed code by default"

_EVERY_TOOL_PRESENT = {
    "kubectl": "/usr/bin/kubectl",
    "helm": "/usr/bin/helm",
    "docker": "/usr/bin/docker",
    "git": "/usr/bin/git",
    "python": "/usr/bin/python",
    "python3": "/usr/bin/python3",
    "curl": "/usr/bin/curl",
    "bash": "/bin/bash",
    "sh": "/bin/sh",
    "buzz": "/usr/bin/buzz",
}


def _which_finds_nothing(cmd: str, *_args: Any, **_kwargs: Any) -> str | None:
    """Stand in for an empty ``PATH``."""
    return None


def _which_explodes(cmd: str, *_args: Any, **_kwargs: Any) -> str | None:
    """Fail loudly if ``PATH`` is walked when the caller already supplied tools."""
    raise AssertionError(f"PATH was walked for {cmd!r} despite an explicit tool map")


def test_empty_path_warns_about_curl_and_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no probed tool on PATH, the default (no-argument) call reports every gap."""
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    monkeypatch.setattr(shutil, "which", _which_finds_nothing)

    facts = capability_warning_facts()

    # Every probed tool resolves to "" — asserted without pinning the tool list,
    # so adding a probe target later does not break this case.
    assert set(installed_tools().values()) == {""}
    assert facts["shell_available"] is False
    assert facts["network_egress"] is False
    assert facts["capability_warnings"] == [
        _CURL_WARNING,
        _NO_SHELL_WARNING,
        _NETWORK_WARNING,
    ]


def test_sh_without_bash_still_counts_as_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """``sh`` alone satisfies the shell check — only losing both tools is a gap."""
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)

    facts = capability_warning_facts({**_EVERY_TOOL_PRESENT, "bash": ""})

    assert facts["shell_available"] is True
    assert _NO_SHELL_WARNING not in facts["capability_warnings"]


def test_no_warnings_when_tools_present_and_network_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully equipped environment with egress opted in produces an empty list."""
    monkeypatch.setenv(OPENSRE_ALLOW_NETWORK_ENV, "1")

    facts = capability_warning_facts(_EVERY_TOOL_PRESENT)

    assert facts["network_egress"] is True
    assert facts["shell_available"] is True
    assert facts["capability_warnings"] == []


def test_explicit_tool_map_does_not_walk_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers that already probed PATH must not pay for a second walk."""
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    monkeypatch.setattr(shutil, "which", _which_explodes)

    facts = capability_warning_facts(_EVERY_TOOL_PRESENT)

    assert facts["capability_warnings"] == [_NETWORK_WARNING]
