"""Root pytest configuration — loads .env for all test directories."""

from __future__ import annotations

import os
from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest

from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
)
from config.constants.paths import PROJECT_ROOT
from config.grafana_cloud import load_env
from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

_ENV_PATH = PROJECT_ROOT / ".env"

# Private opensre-infra-aws submodule paths. Without
# ``git submodule update --init platform/deployment_multi_tenant``, collection of
# these trees fails for community/fork CI.
_OPENSRE_INFRA_AWS_TEST_MARKERS = (
    "/tests/platform/deployment_multi_tenant",
    "/tests/deployment/control_plane",
)
_OPENSRE_INFRA_AWS_AVAILABLE = (
    find_spec("platform.deployment_multi_tenant.lambda_control_plane") is not None
)

if not _OPENSRE_INFRA_AWS_AVAILABLE:
    # Relative to this conftest — covers directory discovery.
    collect_ignore_glob = [
        "platform/deployment_multi_tenant/*",
        "deployment/control_plane/*",
    ]


def pytest_configure(config: pytest.Config) -> None:
    """Drop explicit CLI paths that need the private submodule when it is absent."""
    _load_env()
    _disable_sentry()
    _mark_tests_for_analytics()
    if _OPENSRE_INFRA_AWS_AVAILABLE:
        return
    kept: list[str] = []
    for arg in config.args:
        normalized = Path(arg).resolve().as_posix()
        if any(marker in normalized for marker in _OPENSRE_INFRA_AWS_TEST_MARKERS):
            continue
        kept.append(arg)
    config.args = kept


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
