"""Verify ``opensre update`` emits the expected ``update_*`` events."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from app.cli.commands import general as general_module
from app.cli.commands.general import update_command


@pytest.fixture(autouse=True)
def _capture_analytics(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        general_module,
        "capture_update_started",
        lambda **kwargs: events.append(("started", dict(kwargs))),
    )
    monkeypatch.setattr(
        general_module,
        "capture_update_completed",
        lambda **kwargs: events.append(("completed", dict(kwargs))),
    )
    monkeypatch.setattr(
        general_module,
        "capture_update_failed",
        lambda **kwargs: events.append(("failed", dict(kwargs))),
    )
    return events


def test_update_check_only_emits_started_then_completed(
    monkeypatch: pytest.MonkeyPatch,
    _capture_analytics: list[tuple[str, dict[str, object]]],
) -> None:
    monkeypatch.setattr("app.cli.support.update.run_update", lambda **_kw: 0)

    result = CliRunner().invoke(update_command, ["--check"])

    assert result.exit_code == 0
    assert _capture_analytics == [
        ("started", {"check_only": True}),
        ("completed", {"check_only": True, "updated": False}),
    ]


def test_update_real_install_emits_completed_with_updated_true(
    monkeypatch: pytest.MonkeyPatch,
    _capture_analytics: list[tuple[str, dict[str, object]]],
) -> None:
    monkeypatch.setattr("app.cli.support.update.run_update", lambda **_kw: 0)
    monkeypatch.setattr(general_module, "is_yes", lambda: True)

    result = CliRunner().invoke(update_command, ["--yes"])

    assert result.exit_code == 0
    assert _capture_analytics == [
        ("started", {"check_only": False}),
        ("completed", {"check_only": False, "updated": True}),
    ]


def test_update_failure_emits_started_then_failed(
    monkeypatch: pytest.MonkeyPatch,
    _capture_analytics: list[tuple[str, dict[str, object]]],
) -> None:
    def _boom(**_kwargs: object) -> int:
        raise RuntimeError("network down")

    monkeypatch.setattr("app.cli.support.update.run_update", _boom)
    monkeypatch.setattr(general_module, "is_yes", lambda: False)

    result = CliRunner().invoke(update_command, [])

    assert isinstance(result.exception, RuntimeError)
    assert _capture_analytics == [
        ("started", {"check_only": False}),
        ("failed", {"check_only": False, "reason": "RuntimeError"}),
    ]
