"""Regression borders: only Slack org turns may change, and only their own data.

Three surfaces share one process and one filesystem: the local CLI, Telegram,
and Slack. Scoping was introduced for Slack alone, so each test here fixes one
edge of that boundary — either "this surface is untouched" or "this Slack turn
cannot reach anything but its own".

The deployed Slack task always has ``OPENSRE_CONTEXT_ROOT`` set, so most tests
configure the mount and then assert what *does not* reach it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session.persistence import paths as session_paths
from gateway.slack.principal import slack_scope
from gateway.storage.db import bindings_db_path, connect_bindings_db, default_gateway_db_path
from gateway.storage.session.bindings import SessionBindingStore

ACME = Principal.org("org_acme")
GLOBEX = Principal.org("org_globex")
ALICE = "U_ALICE"
BOB = "U_BOB"

# A value unmistakable if it ever surfaces where it should not.
ACME_SECRET = "datadog-key-BELONGS-TO-ACME-ONLY"

_TELEGRAM = "telegram"
_SLACK = "slack"


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway host root, with no mount configured."""
    root = tmp_path / "host"
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", root)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    return root


@pytest.fixture
def mounted(host: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A deployed Slack task: host root plus Acme's mounted volume.

    The mount names its owner, which the deployed silo always does; a mount with
    no declared owner is refused.
    """
    mount = host.parent / "workspace" / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)
    return mount


def _every_context_path() -> list[Path]:
    """Every path that follows the bound scope."""
    return [
        paths.opensre_home(),
        paths.session_home(),
        paths.integrations_store_path(),
        paths.get_memory_dir(),
        session_paths.sessions_dir(),
        bindings_db_path(),
    ]


# ── Border 1: the local CLI is untouched ────────────────────────────────────


def test_cli_paths_are_the_plain_host_layout(host: Path) -> None:
    # Arrange: a terminal run binds no scope.

    # Act
    resolved = _every_context_path()

    # Assert: no org or user directory appears anywhere.
    assert paths.opensre_home() == host
    assert paths.session_home() == host
    assert paths.integrations_store_path() == host / "integrations.json"
    assert session_paths.sessions_dir() == host / "sessions"
    for path in resolved:
        assert paths.ORGS_DIR_NAME not in path.parts
        assert paths.USERS_DIR_NAME not in path.parts


@pytest.mark.usefixtures("host")
def test_cli_is_unaffected_by_a_configured_mount(mounted: Path) -> None:
    """A CLI process on a host that also runs Slack must stay on the host root."""
    # Act: nothing bound, but the mount env is present.
    resolved = _every_context_path()

    # Assert: the customer's volume is never touched.
    for path in resolved:
        assert mounted not in path.parents
        assert path != mounted


# ── Border 2: Telegram is untouched ─────────────────────────────────────────


def test_telegram_bindings_keep_the_unscoped_key(host: Path) -> None:
    """Telegram passes no principal or actor, exactly as before scoping."""
    # Arrange
    store = SessionBindingStore(connect_bindings_db(host / "bindings.db"))

    # Act: bind the way gateway/telegram does, then read it back the same way.
    store.bind(platform=_TELEGRAM, chat_id="42", session_id="tg-1")
    resolved = store.get_session_id(platform=_TELEGRAM, chat_id="42")
    store.close()

    # Assert
    assert resolved == "tg-1"


def test_telegram_writes_nothing_to_a_customer_volume(host: Path, mounted: Path) -> None:
    """Telegram shares the Slack task but names no organization."""
    # Act: a Telegram turn binds no scope.
    sessions = session_paths.sessions_dir()
    bindings = bindings_db_path()
    memory = paths.get_memory_dir()

    # Assert: all of it stays on ephemeral host storage, not the customer's disk.
    for path in (sessions, bindings, memory):
        assert mounted not in path.parents
    assert sessions == host / "sessions"


def test_a_slack_org_cannot_hijack_a_telegram_binding(host: Path) -> None:
    """Same chat id on two platforms and two scopes must stay separate rows."""
    # Arrange: Telegram binds chat "42" unscoped.
    store = SessionBindingStore(connect_bindings_db(host / "bindings.db"))
    store.bind(platform=_TELEGRAM, chat_id="42", session_id="tg-session")

    # Act: a Slack org binds the same chat id under its own principal and actor.
    store.bind(
        platform=_SLACK, chat_id="42", session_id="slack-session", principal=ACME, actor=ALICE
    )

    # Assert: neither read sees the other's session.
    assert store.get_session_id(platform=_TELEGRAM, chat_id="42") == "tg-session"
    assert (
        store.get_session_id(platform=_SLACK, chat_id="42", principal=ACME, actor=ALICE)
        == "slack-session"
    )
    store.close()


def test_an_unscoped_lookup_cannot_read_an_org_binding(host: Path) -> None:
    """The legacy empty-principal key must not match a real organization's row."""
    # Arrange: only an org-scoped binding exists.
    store = SessionBindingStore(connect_bindings_db(host / "bindings.db"))
    store.bind(
        platform=_SLACK, chat_id="C1:100.1", session_id="acme-session", principal=ACME, actor=ALICE
    )

    # Act: read it the way a pre-scoping caller would.
    leaked = store.get_session_id(platform=_SLACK, chat_id="C1:100.1")
    store.close()

    # Assert
    assert leaked is None


# ── Border 3: one Slack org cannot reach another ────────────────────────────


@pytest.mark.usefixtures("host")
def test_two_orgs_never_share_a_context_path() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme = _every_context_path()
    with bound_storage_scope(slack_scope(GLOBEX, ALICE)):
        globex = _every_context_path()

    # Assert: no path is shared, and neither root contains the other.
    assert set(map(str, acme)).isdisjoint(map(str, globex))


@pytest.mark.usefixtures("host")
def test_one_orgs_credentials_never_appear_under_another() -> None:
    # Arrange: Acme stores a credential carrying a distinctive marker.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme_store = paths.integrations_store_path()
    acme_store.parent.mkdir(parents=True, exist_ok=True)
    acme_store.write_text(ACME_SECRET, encoding="utf-8")

    # Act: read everything Globex can reach under its own root.
    with bound_storage_scope(slack_scope(GLOBEX, BOB)):
        globex_root = paths.opensre_home()
    globex_root.mkdir(parents=True, exist_ok=True)
    visible = [p.read_text(encoding="utf-8") for p in globex_root.rglob("*") if p.is_file()]

    # Assert: the marker is nowhere in Globex's view.
    assert not any(ACME_SECRET in body for body in visible)


def test_org_bindings_are_isolated_by_principal(host: Path) -> None:
    # Arrange: two orgs bind the same conversation key.
    store = SessionBindingStore(connect_bindings_db(host / "bindings.db"))
    store.bind(platform=_SLACK, chat_id="C1:1.0", session_id="acme", principal=ACME, actor=ALICE)
    store.bind(
        platform=_SLACK, chat_id="C1:1.0", session_id="globex", principal=GLOBEX, actor=ALICE
    )

    # Act
    acme_session = store.get_session_id(
        platform=_SLACK, chat_id="C1:1.0", principal=ACME, actor=ALICE
    )
    globex_session = store.get_session_id(
        platform=_SLACK, chat_id="C1:1.0", principal=GLOBEX, actor=ALICE
    )
    store.close()

    # Assert
    assert acme_session == "acme"
    assert globex_session == "globex"


# ── Border 4: inside one org, users are separate but credentials are shared ──


@pytest.mark.usefixtures("host")
def test_members_share_credentials_and_separate_conversations() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        alice_creds, alice_sessions = paths.integrations_store_path(), paths.session_home()
    with bound_storage_scope(slack_scope(ACME, BOB)):
        bob_creds, bob_sessions = paths.integrations_store_path(), paths.session_home()

    # Assert: one set of team credentials, two private conversation roots.
    assert alice_creds == bob_creds
    assert alice_sessions != bob_sessions
    assert BOB not in str(alice_sessions)


def test_members_get_distinct_sessions_in_one_thread(host: Path) -> None:
    """Each Slack user keeps their own history, as chosen for this layout."""
    # Arrange: Alice and Bob speak in the same thread.
    store = SessionBindingStore(connect_bindings_db(host / "bindings.db"))
    thread = "T1:C1:100.1"

    # Act
    alice_session = store.rotate(platform=_SLACK, chat_id=thread, principal=ACME, actor=ALICE)
    bob_session = store.rotate(platform=_SLACK, chat_id=thread, principal=ACME, actor=BOB)

    # Assert: separate sessions, and each still resolves to its own.
    assert alice_session != bob_session
    assert (
        store.get_session_id(platform=_SLACK, chat_id=thread, principal=ACME, actor=ALICE)
        == alice_session
    )
    assert store.has_any_actor_binding(platform=_SLACK, chat_id=thread, principal=ACME)
    store.close()


# ── Border 5: the install catalog is host-level, never per customer ─────────


def test_install_catalog_stays_off_every_customer_volume(host: Path, mounted: Path) -> None:
    """It answers "which org is this team?", so it cannot live inside one org."""
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        installs = default_gateway_db_path()
        bindings = bindings_db_path()

    # Assert: bindings follow the customer, the catalog does not.
    assert installs == host / "gateway" / "state.db"
    assert bindings == mounted / "gateway" / "bindings.db"
    assert mounted not in installs.parents


def test_install_catalog_holds_no_binding_rows(host: Path) -> None:
    """The two databases are split, so neither can silently answer for the other."""
    # Arrange
    from gateway.storage.db import connect_gateway_db

    conn = connect_gateway_db(host / "gateway" / "state.db")

    # Act
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()

    # Assert
    assert "slack_installs" in tables
    assert "gateway_session_bindings" not in tables


# ── Border 6: identifiers cannot escape their directory ─────────────────────


@pytest.mark.parametrize("hostile", ["../../etc", "..", "a/b", "with space"])
@pytest.mark.usefixtures("host")
def test_hostile_org_ids_cannot_escape(hostile: str) -> None:
    with (
        bound_storage_scope(StorageScope(principal=Principal.org(hostile), actor=Actor(id=ALICE))),
        pytest.raises(paths.UnsafePathSegmentError),
    ):
        paths.opensre_home()


@pytest.mark.parametrize("hostile", ["../../etc", "..", "a/b"])
@pytest.mark.usefixtures("host")
def test_hostile_user_ids_cannot_escape(hostile: str) -> None:
    with (
        bound_storage_scope(StorageScope(principal=ACME, actor=Actor(id=hostile))),
        pytest.raises(paths.UnsafePathSegmentError),
    ):
        paths.session_home()


# ── Border 7: the scope never outlives its turn ─────────────────────────────


def test_scope_is_released_when_the_turn_ends(host: Path) -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        inside = paths.session_home()
    after = paths.session_home()

    # Assert: the next turn on this thread starts unscoped, not on Acme's data.
    assert inside != after
    assert after == host


@pytest.mark.usefixtures("host")
def test_scope_does_not_leak_across_nested_turns() -> None:
    # Act: a second organization's turn nested inside the first.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        with bound_storage_scope(slack_scope(GLOBEX, BOB)):
            inner = paths.opensre_home()
        outer_again = paths.opensre_home()

    # Assert: the outer turn is restored, not left pointing at Globex.
    assert inner != outer_again
    assert GLOBEX.id not in str(outer_again)


@pytest.mark.usefixtures("host")
def test_bindings_connection_follows_the_bound_org() -> None:
    """One worker serving two orgs must not reuse the first org's database."""
    # Arrange: the store resolves its database per call, not at construction.
    from gateway.storage.db import BindingsConnections

    store = SessionBindingStore(BindingsConnections())

    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        store.bind(
            platform=_SLACK, chat_id="C1:1.0", session_id="acme", principal=ACME, actor=ALICE
        )
    with bound_storage_scope(slack_scope(GLOBEX, ALICE)):
        globex_view = store.get_session_id(
            platform=_SLACK, chat_id="C1:1.0", principal=ACME, actor=ALICE
        )
    store.close()

    # Assert: Globex's database has no row from Acme's.
    assert globex_view is None


@pytest.mark.usefixtures("host")
def test_each_org_gets_its_own_bindings_database() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme_db = bindings_db_path()
    with bound_storage_scope(slack_scope(GLOBEX, ALICE)):
        globex_db = bindings_db_path()

    # Assert
    assert acme_db != globex_db


def test_legacy_host_database_is_readable_after_the_split(host: Path) -> None:
    """A pre-split file must still open rather than error on the new schema."""
    # Arrange: a database holding both tables, as written before the split.
    legacy_path = host / "gateway" / "state.db"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(legacy_path)
    legacy.executescript(
        """
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL, chat_id TEXT NOT NULL, principal_id TEXT NOT NULL,
            actor_id TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id, principal_id, actor_id)
        );
        INSERT INTO gateway_session_bindings VALUES ('telegram','42','','','tg-legacy',1.0);
        """
    )
    legacy.commit()
    legacy.close()

    # Act
    store = SessionBindingStore(connect_bindings_db(host / "gateway" / "bindings.db"))
    resolved = store.get_session_id(platform=_TELEGRAM, chat_id="42")
    store.close()

    # Assert: the existing Telegram conversation survives the upgrade.
    assert resolved == "tg-legacy"


def test_telegram_runtime_wiring_can_read_and_write_bindings(
    host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the store Telegram actually builds, not a hand-made one.

    The previous test built a store directly, so it kept passing when the real
    wiring pointed at a database without the bindings table.
    """
    # Arrange: the store the Telegram runtime constructs at startup.
    from gateway.storage.session.binding_store import open_binding_store

    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    bindings = open_binding_store()

    # Act: bind and resolve a Telegram chat the way the runtime does.
    bindings.bind(platform=_TELEGRAM, chat_id="42", session_id="tg-wired")
    resolved = bindings.get_session_id(platform=_TELEGRAM, chat_id="42")
    bindings.close()

    # Assert: the table exists and the round trip works on the host root.
    assert resolved == "tg-wired"
    assert (host / "gateway" / "bindings.db").is_file()


def test_pre_scoping_bindings_are_adopted(host: Path) -> None:
    """A database written by main has no principal or actor columns at all.

    Selecting them outright raises, so a column-blind adopt silently drops every
    conversation on the first start after upgrade.
    """
    # Arrange: exactly main's table shape.
    legacy_path = host / "gateway" / "state.db"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(legacy_path)
    legacy.executescript(
        """
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id)
        );
        INSERT INTO gateway_session_bindings VALUES ('telegram','42','tg-from-main',1.0);
        """
    )
    legacy.commit()
    legacy.close()

    # Act
    store = SessionBindingStore(connect_bindings_db(host / "gateway" / "bindings.db"))
    resolved = store.get_session_id(platform=_TELEGRAM, chat_id="42")
    store.close()

    # Assert: the pre-upgrade conversation carries over as an unscoped binding.
    assert resolved == "tg-from-main"


def test_silo_bindings_are_adopted_from_the_host_onto_the_mount(host: Path, mounted: Path) -> None:
    """A silo's rows sit on the host, while its new bindings live on the volume."""
    # Arrange: a pre-cutover host database.
    legacy_path = host / "gateway" / "state.db"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(legacy_path)
    legacy.executescript(
        """
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL, chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id)
        );
        INSERT INTO gateway_session_bindings VALUES ('slack','C1:1.0','silo-session',1.0);
        """
    )
    legacy.commit()
    legacy.close()

    # Act: the first Slack turn after cutover opens the mounted database.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        store = SessionBindingStore(connect_bindings_db())
        resolved = store.get_session_id(platform=_SLACK, chat_id="C1:1.0")
        store.close()

    # Assert: the conversation is carried onto the volume, not lost.
    assert resolved == "silo-session"
    assert (mounted / "gateway" / "bindings.db").is_file()


def test_a_mount_without_a_declared_owner_is_refused(
    host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unidentified volume could belong to anyone, so no turn may write to it."""
    # Arrange: the mount is configured but nothing says which org owns it.
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(host.parent / "memories"))
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)

    # Act / Assert
    with (
        bound_storage_scope(slack_scope(ACME, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError),
    ):
        paths.opensre_home()


@pytest.mark.usefixtures("mounted")
def test_a_turn_for_another_org_cannot_use_the_mount() -> None:
    """The volume is chrooted to one org; a different org must never reach it."""
    # Act / Assert: the mount belongs to Acme, the turn belongs to Globex.
    with (
        bound_storage_scope(slack_scope(GLOBEX, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError),
    ):
        paths.opensre_home()
