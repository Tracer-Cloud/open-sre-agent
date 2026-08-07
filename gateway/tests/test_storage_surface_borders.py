"""Regression: Slack org scoping must not leak into peer surfaces.

Borders under test
------------------
* Slack / Discord / Telegram — each binds ``StorageScope`` via its own
  ``*.principal`` module (peer isolation; no cross-imports).
* CLI / interactive shell — stay unbound; host ``~/.opensre``; bindings omit
  principal/actor (legacy empty ids).
* Shared ``gateway.core.storage`` — transport-neutral; no Slack resolve exports.
* Actors in one org — private sessions; shared integrations; no cross-user
  corruption.
* Orgs — never share a context root or binding row.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from gateway.core.storage import FileBindingStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def _scope(org_id: str, user_id: str) -> StorageScope:
    return StorageScope(principal=Principal.org(org_id), actor=Actor(id=user_id))


@pytest.fixture(autouse=True)
def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    return tmp_path


# ── SoC: import / package borders ───────────────────────────────────────────


def _python_files(package: str) -> list[Path]:
    root = REPO_ROOT / Path(*package.split("."))
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_telegram_package_never_imports_slack_or_discord_principal() -> None:
    """Telegram uses its own principal module — never Slack/Discord peers."""
    banned = (
        "gateway.transports.slack.principal",
        "gateway.transports.slack.installs",
        "gateway.transports.discord.principal",
        "gateway.core.storage.principal_resolve",
        "gateway.core.storage.slack_installs",
    )
    offenders: list[str] = []
    for path in _python_files("gateway.transports.telegram"):
        imported = _imported_modules(path)
        for name in banned:
            if name in imported or any(n.startswith(name + ".") for n in imported):
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {name}")
    assert offenders == [], "Telegram crossed a peer principal border:\n" + "\n".join(offenders)


def test_surfaces_never_import_slack_principal_resolve() -> None:
    """CLI / interactive shell stay unbound; no Slack resolve on those surfaces."""
    banned_prefixes = (
        "gateway.transports.slack.principal",
        "gateway.core.storage.principal_resolve",
    )
    offenders: list[str] = []
    for package in ("surfaces.cli", "surfaces.interactive_shell"):
        for path in _python_files(package):
            for name in _imported_modules(path):
                if any(name == p or name.startswith(p + ".") for p in banned_prefixes):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} → {name}")
    assert offenders == []


def test_gateway_storage_package_does_not_export_slack_resolve() -> None:
    """Shared storage stays transport-neutral after the Slack SoC move."""
    storage = importlib.import_module("gateway.core.storage")
    leaked = [
        name
        for name in (
            "resolve_slack_scope",
            "resolve_slack_principal",
            "PrincipalResolutionError",
            "get_slack_install",
            "upsert_slack_install",
            "SlackInstall",
        )
        if hasattr(storage, name)
    ]
    assert leaked == [], f"gateway.core.storage still exports Slack APIs: {leaked}"


def test_config_principal_has_no_slack_named_factories() -> None:
    """Leaf identity types stay vendor-neutral; Slack factories live in gateway.transports.slack."""
    from config import principal as principal_mod

    assert not hasattr(principal_mod.Actor, "slack")
    assert not hasattr(principal_mod.StorageScope, "for_slack_member")
    assert hasattr(importlib.import_module("gateway.transports.slack.principal"), "slack_scope")


# ── Slack org users: isolation ──────────────────────────────────────────────


def test_two_slack_actors_do_not_share_or_corrupt_sessions(_host: Path) -> None:
    alice = _scope("org_acme", "U_ALICE")
    bob = _scope("org_acme", "U_BOB")
    with bound_storage_scope(alice):
        alice_home = paths.session_home()
        alice_integ = paths.integrations_store_path()
        (alice_home / "sessions").mkdir(parents=True)
        (alice_home / "sessions" / "alice.jsonl").write_text("alice-only\n", encoding="utf-8")
    with bound_storage_scope(bob):
        bob_home = paths.session_home()
        bob_integ = paths.integrations_store_path()
        (bob_home / "sessions").mkdir(parents=True)
        (bob_home / "sessions" / "bob.jsonl").write_text("bob-only\n", encoding="utf-8")

    assert alice_integ == bob_integ
    assert alice_home != bob_home
    assert (alice_home / "sessions" / "alice.jsonl").read_text(encoding="utf-8") == "alice-only\n"
    assert not (alice_home / "sessions" / "bob.jsonl").exists()
    assert (bob_home / "sessions" / "bob.jsonl").read_text(encoding="utf-8") == "bob-only\n"
    assert not (bob_home / "sessions" / "alice.jsonl").exists()


def test_two_orgs_do_not_share_integrations_or_sessions(_host: Path) -> None:
    with bound_storage_scope(_scope("org_acme", "U_ALICE")):
        acme_integ = paths.integrations_store_path()
        acme_sess = paths.session_home()
        acme_integ.parent.mkdir(parents=True, exist_ok=True)
        acme_integ.write_text('{"acme": true}', encoding="utf-8")
    with bound_storage_scope(_scope("org_globex", "U_ALICE")):
        globex_integ = paths.integrations_store_path()
        globex_sess = paths.session_home()
        globex_integ.parent.mkdir(parents=True, exist_ok=True)
        globex_integ.write_text('{"globex": true}', encoding="utf-8")

    assert acme_integ != globex_integ
    assert acme_sess != globex_sess
    assert acme_integ.read_text(encoding="utf-8") == '{"acme": true}'
    assert globex_integ.read_text(encoding="utf-8") == '{"globex": true}'


def test_slack_and_telegram_scoped_bindings_stay_isolated(
    tmp_path: Path,
) -> None:
    store = FileBindingStore(tmp_path / "bindings.json")
    org = Principal.org("org_acme")
    store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="slack-alice",
        principal=org,
        actor="U_ALICE",
    )
    store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="slack-bob",
        principal=org,
        actor="U_BOB",
    )
    store.bind(
        platform="telegram",
        chat_id="42",
        session_id="tg-alice",
        principal=org,
        actor="tg-42",
    )

    assert (
        store.get_session_id(platform="slack", chat_id="T:C:1", principal=org, actor="U_ALICE")
        == "slack-alice"
    )
    assert (
        store.get_session_id(platform="slack", chat_id="T:C:1", principal=org, actor="U_BOB")
        == "slack-bob"
    )
    assert (
        store.get_session_id(platform="telegram", chat_id="42", principal=org, actor="tg-42")
        == "tg-alice"
    )
    # Unscoped lookups must not answer scoped rows.
    assert store.get_session_id(platform="slack", chat_id="T:C:1") is None
    assert store.get_session_id(platform="telegram", chat_id="42") is None
    # Cross-platform scoped lookup must miss.
    assert (
        store.get_session_id(platform="telegram", chat_id="42", principal=org, actor="U_ALICE")
        is None
    )
    store.close()


# ── Unbound CLI surfaces: mount ignored ─────────────────────────────────────


def test_unbound_surfaces_ignore_context_root_mount(
    _host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = _host / "workspace" / "memories"
    mount.mkdir(parents=True)
    (mount / "integrations.json").write_text('{"customer": true}', encoding="utf-8")
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_acme")

    assert current_scope() is None
    assert paths.opensre_home() == _host
    assert paths.session_home() == _host
    assert paths.integrations_store_path() == _host / "integrations.json"
    assert not (_host / "integrations.json").exists()
    # Customer file must remain untouched by unbound reads.
    assert (mount / "integrations.json").read_text(encoding="utf-8") == '{"customer": true}'


def test_individual_principal_stays_on_host_home_even_when_bound(
    _host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-org principals must not nest under orgs/ or consume the mount."""
    mount = _host / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_acme")
    individual = StorageScope(
        principal=Principal.individual("solo_user"),
        actor=Actor(id="solo_user"),
    )
    with bound_storage_scope(individual):
        assert paths.opensre_home() == _host
        assert paths.session_home() == _host
        assert paths.ORGS_DIR_NAME not in paths.opensre_home().parts
        assert mount not in paths.integrations_store_path().parents
