"""Tests for the ``get_feed``/``edit_message`` additions to ``BuzzClient``.

Milestone 1 (#4756) shipped the client without a companion test file — the
pre-existing ``probe_access``/``send_message`` paths are uncovered debt, out
of scope here. This file covers only what the two-way transport PR added.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from integrations.buzz.client import BuzzClient
from integrations.config_models import BuzzConfig


def _config() -> BuzzConfig:
    return BuzzConfig(private_key="k", relay_url="http://localhost:3000", buzz_path="buzz")


def _fake_run(
    *, returncode: int, stdout: str = "", stderr: str = ""
) -> Callable[..., subprocess.CompletedProcess]:
    def _run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return _run


def _client(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int, stdout: str = "", stderr: str = ""
) -> BuzzClient:
    client = BuzzClient(_config())
    monkeypatch.setattr(client, "_resolved_path", lambda: "/usr/bin/buzz")
    monkeypatch.setattr(
        subprocess, "run", _fake_run(returncode=returncode, stdout=stdout, stderr=stderr)
    )
    return client


def test_get_feed_returns_events_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch, returncode=0, stdout='[{"id": "ev1", "pubkey": "pk", "tags": [["h", "c"]]}]'
    )

    result = client.get_feed(since=100, types="mentions")

    assert result["success"] is True
    assert result["events"][0]["id"] == "ev1"


def test_get_feed_reports_relay_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch, returncode=2, stderr='{"error": "network", "message": "relay unreachable"}'
    )

    result = client.get_feed(since=100)

    assert result["success"] is False
    assert "relay unreachable" in result["error"]
    assert result["events"] == []


def test_edit_message_requires_event_id() -> None:
    client = BuzzClient(_config())

    result = client.edit_message(event_id="", content="x")

    assert result["success"] is False


def test_edit_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, returncode=0, stdout="{}")

    result = client.edit_message(event_id="a" * 64, content="updated")

    assert result["success"] is True
