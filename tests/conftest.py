"""Root pytest configuration — loads .env for all test directories."""

import os
from pathlib import Path

import pytest

from config.grafana_cloud import load_env
from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def _load_env() -> None:
    if _ENV_PATH.exists():
        load_env(_ENV_PATH, override=True)


def _disable_sentry() -> None:
    os.environ["OPENSRE_SENTRY_DISABLED"] = "1"


def _mark_tests_for_analytics() -> None:
    os.environ["OPENSRE_NO_TELEMETRY"] = "1"
    os.environ["OPENSRE_INVESTIGATION_SOURCE"] = "test"


_load_env()
_disable_sentry()
_mark_tests_for_analytics()


@pytest.fixture(autouse=True)
def _restore_os_environ():
    """Snapshot and restore ``os.environ`` around every test.

    Some app code mutates the live process environment as a side effect — most
    notably ``sync_provider_env``, which calls ``os.environ.pop``/``update`` to
    drop stale provider keys (including other providers' API keys such as
    ``OPENAI_API_KEY``) when switching the active LLM provider. Tests that
    exercise those paths (the onboarding wizard, provider switching, etc.) do
    not ``monkeypatch`` every key the code touches, so without this snapshot the
    mutations leak across tests sharing an xdist worker. The leaked deletion of
    ``OPENAI_API_KEY`` made later ``live_llm`` planner contracts resolve the
    fallback (credit-exhausted anthropic) provider and skip. Restoring the full
    environment after each test contains that whole class of leakage.

    Module-/session-scoped fixtures still work: their env mutations happen
    before this function-scoped snapshot is taken on the first test and are
    never removed, so the snapshot carries them forward.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _disable_system_keyring(request, monkeypatch) -> None:
    """Keep tests isolated from any real developer keychain entries."""
    if request.node.get_closest_marker("live_llm") is not None:
        return
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


def pytest_configure(config):
    """Pytest hook — keep env available for collection and execution."""
    _load_env()
    _disable_sentry()
    _mark_tests_for_analytics()
