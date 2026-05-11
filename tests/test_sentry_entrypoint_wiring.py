"""Wiring tests for issue #1477 — init_sentry coverage on standalone entrypoints."""

from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clear_init_cache() -> None:
    from app.utils import sentry_sdk as sentry_mod

    sentry_mod._init_sentry_once.cache_clear()


def _record_init(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    calls: list[str | None] = []

    def _stub(entrypoint: str | None = None) -> None:
        calls.append(entrypoint)

    monkeypatch.setattr("app.utils.sentry_sdk.init_sentry", _stub)
    return calls


def _reload(module_name: str) -> Any:
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_graph_pipeline_initializes_sentry_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_init(monkeypatch)
    _reload("app.graph_pipeline")
    assert "graph_pipeline" in calls


def test_entrypoints_sdk_initializes_sentry_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_init(monkeypatch)
    _reload("app.entrypoints.sdk")
    assert "entrypoints.sdk" in calls


def test_daily_update_main_initializes_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations import daily_update

    calls: list[str | None] = []
    monkeypatch.setattr(
        daily_update, "init_sentry", lambda entrypoint=None: calls.append(entrypoint)
    )
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    assert daily_update.main() == 1
    assert calls == ["integrations.daily_update"]


def test_github_issue_comments_main_initializes_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations import github_issue_comments

    calls: list[str | None] = []
    monkeypatch.setattr(
        github_issue_comments, "init_sentry", lambda entrypoint=None: calls.append(entrypoint)
    )
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    assert github_issue_comments.main() == 1
    assert calls == ["integrations.github_issue_comments"]


def test_grafana_seed_main_initializes_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.cli.wizard import grafana_seed

    calls: list[str | None] = []
    monkeypatch.setattr(
        grafana_seed, "init_sentry", lambda entrypoint=None: calls.append(entrypoint)
    )
    monkeypatch.setattr(grafana_seed, "seed_logs", lambda: None)

    assert grafana_seed.main() == 0
    assert calls == ["wizard.grafana_seed"]


def test_analytics_install_main_block_initializes_sentry() -> None:
    import subprocess
    import sys

    script = (
        "import app.utils.sentry_sdk as s\n"
        "calls = []\n"
        "s.init_sentry = lambda entrypoint=None: calls.append(entrypoint)\n"
        "import app.analytics.provider as p\n"
        "p.capture_install_detected_if_needed = lambda *a, **k: None\n"
        "p.shutdown_analytics = lambda *a, **k: None\n"
        "import runpy\n"
        "try:\n"
        "    runpy.run_module('app.analytics.install', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "assert calls == ['analytics.install'], calls\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
