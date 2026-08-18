"""Per-org / per-member path resolution for multi-tenant Slack context."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.constants.paths import (
    ORGS_DIR_NAME,
    USERS_DIR_NAME,
    UnsafePathSegmentError,
    integrations_store_path,
    opensre_home,
    session_home,
)
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session.persistence.paths import sessions_dir


def _imported_opensre_home(env: dict[str, str]) -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config.constants.paths import OPENSRE_HOME_DIR; print(OPENSRE_HOME_DIR)",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    return Path(completed.stdout.strip())


def test_opensre_home_defaults_to_dot_opensre_under_user_home(tmp_path: Path) -> None:
    # Arrange
    env = os.environ.copy()
    env.pop("OPENSRE_HOME", None)
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)

    # Act
    home = _imported_opensre_home(env)

    # Assert
    assert home == tmp_path / ".opensre"


def test_opensre_home_uses_environment_override(tmp_path: Path) -> None:
    # Arrange
    override = tmp_path / "relocated-opensre"
    env = os.environ.copy()
    env["OPENSRE_HOME"] = str(override)

    # Act
    home = _imported_opensre_home(env)

    # Assert
    assert home == override


@pytest.fixture
def host_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".opensre"
    home.mkdir()
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", home)
    return home


def test_unbound_paths_stay_flat(host_home: Path) -> None:
    assert opensre_home() == host_home
    assert session_home() == host_home
    assert integrations_store_path() == host_home / "integrations.json"
    assert sessions_dir() == host_home / "sessions"


def test_org_integrations_shared_member_sessions_private(host_home: Path) -> None:
    org = Principal.org("org_acme")
    alice = StorageScope(principal=org, actor=Actor(id="U_ALICE"))
    bob = StorageScope(principal=org, actor=Actor(id="U_BOB"))

    with bound_storage_scope(alice):
        alice_integ = integrations_store_path()
        alice_sessions = sessions_dir()

    with bound_storage_scope(bob):
        bob_integ = integrations_store_path()
        bob_sessions = sessions_dir()

    org_root = host_home / ORGS_DIR_NAME / "org_acme"
    assert alice_integ == bob_integ == org_root / "integrations.json"
    assert alice_sessions == org_root / USERS_DIR_NAME / "U_ALICE" / "sessions"
    assert bob_sessions == org_root / USERS_DIR_NAME / "U_BOB" / "sessions"
    assert alice_sessions != bob_sessions


def test_two_orgs_get_distinct_integration_paths(host_home: Path) -> None:
    with bound_storage_scope(StorageScope(principal=Principal.org("org_a"), actor=Actor(id="U1"))):
        path_a = integrations_store_path()
    with bound_storage_scope(StorageScope(principal=Principal.org("org_b"), actor=Actor(id="U1"))):
        path_b = integrations_store_path()

    assert path_a == host_home / ORGS_DIR_NAME / "org_a" / "integrations.json"
    assert path_b == host_home / ORGS_DIR_NAME / "org_b" / "integrations.json"
    assert path_a != path_b


def test_unsafe_org_id_rejected(host_home: Path) -> None:
    _ = host_home
    scope = StorageScope(principal=Principal.org("org/../escape"), actor=Actor(id="U1"))
    with bound_storage_scope(scope), pytest.raises(UnsafePathSegmentError):
        opensre_home()


def test_unsafe_actor_id_rejected(host_home: Path) -> None:
    _ = host_home
    scope = StorageScope(principal=Principal.org("org_ok"), actor=Actor(id="U/../escape"))
    with bound_storage_scope(scope), pytest.raises(UnsafePathSegmentError):
        session_home()


def test_integrations_store_writes_under_org_home(
    host_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import store as store_mod

    monkeypatch.setattr(store_mod, "STORE_PATH", None)
    org = Principal.org("org_acme")
    scope = StorageScope(principal=org, actor=Actor(id="U_ALICE"))
    with bound_storage_scope(scope):
        store_mod.upsert_integration(
            "datadog",
            {"credentials": {"api_key": "k", "app_key": "a", "site": "datadoghq.com"}},
        )
        loaded = store_mod.get_integration("datadog")

    assert loaded is not None
    path = host_home / ORGS_DIR_NAME / "org_acme" / "integrations.json"
    assert path.is_file()
    assert not (host_home / "integrations.json").exists()
