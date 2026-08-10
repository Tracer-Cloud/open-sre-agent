"""Root pytest configuration — loads .env for all test directories."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

import config.constants.paths as paths
from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
)
from config.grafana_cloud import load_env
from config.platform_bootstrap import ensure_project_platform_package
from config.secrets.os_keyring import reset_keyring_state

ensure_project_platform_package()

_ENV_PATH = paths.PROJECT_ROOT / ".env"


def pytest_configure(config: pytest.Config) -> None:
    """Prepare the environment every test run depends on."""
    _ = config
    _load_env()
    _disable_sentry()
    _mark_tests_for_analytics()


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
def _reset_gcp_project_discovery() -> None:
    """Clear the memoized Resource Manager listing between tests.

    ``GCP_ADDITIONAL_PROJECTS=discover`` caches one listing per *credential*, and
    the cache key is a hash of the credential fields alone. Almost every GCP
    fixture leaves those empty (ADC is the default auth path), so every such test
    shares one key — the first test to run discovery would decide the project
    allow-list for all the others on that xdist worker, including the ones
    asserting that discovery failed.
    """
    from integrations.gcp.project_discovery import reset_cache

    reset_cache()


@pytest.fixture(autouse=True)
def _disable_system_keyring(request, monkeypatch) -> None:
    """Keep tests isolated from any real developer keychain entries.

    The sticky "keyring unavailable" flag is process-global by design (one probe
    decides for a whole run), so a test that deliberately provokes a backend
    failure would otherwise leak that state into every later test on the same
    xdist worker.
    """
    reset_keyring_state()
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

    Memory storage is also redirected for every test so deterministic prompt
    snapshots never read the developer's real ``~/.opensre/memory`` directory.
    Background memory extraction is disabled by default because most tests use
    tiny fake LLM clients and assert the exact prompt/stream call shape; the
    memory-specific tests remove this env var in their own fixture.

    The wizard/LLM-auth overrides mirror the ``live_llm`` exemption on
    ``_disable_system_keyring`` above: live LLM turn tests need the real
    ``~/.opensre/llm-auth.json`` metadata for CLI-subscription providers, whose
    prompt-safe ``status()`` reads the metadata record directly rather than an
    env var.
    """
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    if request.node.get_closest_marker("live_llm") is not None:
        return
    monkeypatch.setenv("OPENSRE_WIZARD_STORE_PATH", str(tmp_path / "opensre.json"))
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    # Same reasoning for the fallback credential store, which ``host_home()``
    # resolves from this module global at call time: a test that provokes a
    # keyring failure would otherwise write real secrets into the developer's
    # ~/.opensre/credentials.json and leave them there.
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "opensre-home")


@pytest.fixture(autouse=True)
def _reset_setup_state_cache() -> None:
    """Drop the memoized setup block between tests.

    It lives at module scope and is keyed partly on a stat of the scheduler
    stores, so a test whose stores happen to match an earlier one would read
    the earlier block and pass or fail for the wrong reason.
    """
    from platform.setup_state import clear_setup_state_cache

    clear_setup_state_cache()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail hard when nothing was selected (pytest-xdist can still exit 0).

    Reproduces as ``N workers [0 items]`` under ``-n`` when ``-m`` deselects
    everything (e.g. a mangled CI marker that becomes ``false``). Without this,
    CI can go green while running zero tests — especially on large path sets
    where xdist reports warnings and exits 0 instead of NO_TESTS_COLLECTED.
    """
    if session.testscollected != 0:
        return
    if exitstatus in (
        0,
        pytest.ExitCode.OK,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    ):
        session.exitstatus = pytest.ExitCode.NO_TESTS_COLLECTED
