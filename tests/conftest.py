"""Root pytest configuration — loads .env for all test directories."""

import os
from collections.abc import Iterator

import pytest

from config.constants.paths import PROJECT_ROOT
from config.grafana_cloud import load_env
from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

_ENV_PATH = PROJECT_ROOT / ".env"


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
def _harness_ports_per_test() -> Iterator[None]:
    """Wire harness ports before each test; reset after to avoid session leakage."""
    from platform.harness_ports import reset_harness_ports
    from surfaces.interactive_shell.ui.output.boundary import install_harness_ports

    install_harness_ports()
    yield
    reset_harness_ports()


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


@pytest.fixture(autouse=True)
def _isolate_opensre_home_files(request, monkeypatch, tmp_path) -> None:
    """Default-redirect the wizard store and LLM auth metadata files to tmp_path.

    Regression guard for #3721: ``sync_provider_env``/``update_local_llm_selection``
    write ``~/.opensre/opensre.json`` (and credential resolution writes
    ``~/.opensre/llm-auth.json``) with no per-test opt-in required, so any test
    exercising those paths that forgets to monkeypatch ``get_store_path``
    individually silently corrupts the *developer's real* config and credential
    metadata (observed as ``opensre.json`` cycling through unrelated test
    providers, and a valid provider getting marked stale, while ``make
    test-cov`` ran). Setting both overrides here makes every test safe by
    default; a test that needs a specific path can still override it via
    ``monkeypatch`` or by passing an explicit ``path=`` argument.

    Mirrors the ``live_llm`` exemption on ``_disable_system_keyring`` above:
    live LLM turn tests need the real ``~/.opensre/llm-auth.json`` metadata for
    CLI-subscription providers, whose prompt-safe ``status()`` reads the
    metadata record directly rather than an env var.
    """
    if request.node.get_closest_marker("live_llm") is not None:
        return
    monkeypatch.setenv("OPENSRE_WIZARD_STORE_PATH", str(tmp_path / "opensre.json"))
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))


def pytest_configure(config):
    """Pytest hook — keep env available for collection and execution."""
    _load_env()
    _disable_sentry()
    _mark_tests_for_analytics()
