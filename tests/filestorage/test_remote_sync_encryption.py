"""Client-side encryption: the store holds nothing readable, and nothing fails open.

The property under test is that a sync either does the encrypted thing or does
nothing. Every state where the machine and the store disagree about encryption
is refused, in both directions, and a stored object that cannot be opened never
reaches the local file — which the recency conflict rule would otherwise let it
overwrite.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants.filestorage import REMOTE_SYNC_KEY_CACHE_ENV, REMOTE_SYNC_PASSPHRASE_ENV
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.encryption import envelope
from infrastructure.filestorage.encryption.cipher import ManifestCipher
from infrastructure.filestorage.encryption.keys import (
    ScryptParams,
    derive_root_key,
    generate_root_secret,
)
from infrastructure.filestorage.encryption.manifest import (
    MANIFEST_KEY,
    load_manifest,
    parse_manifest,
)
from infrastructure.filestorage.encryption.resolver import resolve_cipher
from infrastructure.filestorage.encryption.rotation import reencrypt, rotate_passphrase
from infrastructure.filestorage.engine import SyncDirection, SyncReport, content_tag, run_sync
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.errors import (
    EncryptedStoreError,
    ManifestMissingError,
    MissingPassphraseError,
    PlaintextStoreError,
    RemoteSyncEncryptionError,
    UndecryptableObjectError,
    WrongPassphraseError,
)
from infrastructure.filestorage.syncable import SyncRoot

PASSPHRASE = "correct horse battery staple"
# Planted in a session. If any transformation ever fails open, this shows up in
# the store's bytes and the assertion below fails loudly.
LEAKED_SECRET = "db-password-CANARY-must-never-reach-the-store"


class FakeObjectStore:
    """In-memory ObjectStore that tags objects the way S3 does."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.modified: dict[str, datetime] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=self.modified.get(key, datetime.now(tz=UTC)),
                etag=content_tag(data),
            )
            for key, data in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data
        self.modified.setdefault(key, datetime.now(tz=UTC))

    def describe(self) -> str:
        return "fake://bucket"


class CrashingStore(FakeObjectStore):
    """Accepts ``remaining_writes`` more writes, then fails like a dropped link."""

    def __init__(self) -> None:
        super().__init__()
        self.remaining_writes: int | None = None

    def put_object(self, key: str, data: bytes) -> None:
        if self.remaining_writes is not None:
            if self.remaining_writes == 0:
                raise RuntimeError("object store went away")
            self.remaining_writes -= 1
        super().put_object(key, data)


@pytest.fixture(autouse=True)
def passphrase_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the passphrase from the environment, never the real keychain.

    The env tier is checked first by :mod:`config.secrets.store`, so this also
    keeps the derived-key cache out of the developer's own secret store.
    """
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, PASSPHRASE)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "sessions").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "sessions" / "abc.jsonl").write_text(
        f'{{"turn": 1, "text": "{LEAKED_SECRET}"}}\n', encoding="utf-8"
    )
    (tmp_path / "memory" / "a-fact.md").write_text("remembered\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def roots(home: Path) -> tuple[SyncRoot, ...]:
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=home / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=home / "memory"),
    )


def _encrypted_sync(
    store: FakeObjectStore, roots: tuple[SyncRoot, ...], direction: SyncDirection
) -> SyncReport:
    """Resolve the gate and run one direction, the way a real surface would."""
    gate = resolve_cipher(store, encrypted=True)
    return run_sync(
        store, direction=direction, roots=roots, cipher=gate.cipher, listing=gate.listing
    )


def _encrypted_push(store: FakeObjectStore, roots: tuple[SyncRoot, ...]) -> None:
    _encrypted_sync(store, roots, SyncDirection.PUSH)


# ── The store holds nothing readable ────────────────────────────────────────


def test_uploaded_objects_carry_no_plaintext(
    roots: tuple[SyncRoot, ...],
) -> None:
    # Arrange
    store = FakeObjectStore()

    # Act
    _encrypted_push(store, roots)

    # Assert: the canary is nowhere in the store, and every mirrored object is
    # a real envelope rather than merely "not obviously readable".
    blob = b"".join(store.objects.values())
    assert LEAKED_SECRET.encode() not in blob
    for key, data in store.objects.items():
        if key != MANIFEST_KEY:
            assert envelope.is_sealed(data), key


def test_round_trip_restores_the_original_bytes(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    # Arrange: one machine pushes, a second one pulls into an empty tree.
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    other = tmp_path / "second-laptop"
    second = (
        SyncRoot(name=SyncRootName.SESSIONS, path=other / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=other / "memory"),
    )

    # Act
    _encrypted_sync(store, second, SyncDirection.PULL)

    # Assert
    assert (other / "sessions" / "abc.jsonl").read_bytes() == (
        home / "sessions" / "abc.jsonl"
    ).read_bytes()


def test_manifest_never_mirrors_onto_the_laptop(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    assert MANIFEST_KEY in store.objects

    # Act
    _encrypted_sync(store, roots, SyncDirection.PULL)

    # Assert: it has no root prefix, so it maps to no local path.
    assert not (home / MANIFEST_KEY).exists()


# ── The deterministic seal keeps change detection working ───────────────────


def test_unchanged_files_are_not_re_uploaded(roots: tuple[SyncRoot, ...]) -> None:
    """A random nonce would make every file differ from its stored tag."""
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)

    # Act
    report = _encrypted_sync(store, roots, SyncDirection.PUSH)

    # Assert
    assert report.uploaded == []
    assert report.skipped == 2


def test_unchanged_files_are_not_re_downloaded(roots: tuple[SyncRoot, ...]) -> None:
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)

    # Act
    report = _encrypted_sync(store, roots, SyncDirection.PULL)

    # Assert
    assert report.downloaded == []


# ── Envelope integrity ──────────────────────────────────────────────────────


def test_an_object_moved_to_another_key_will_not_open() -> None:
    """The object key is authenticated, so copies cannot be passed off."""
    # Arrange
    cipher = ManifestCipher(derive_root_key(generate_root_secret()))
    sealed = cipher.seal("memory/real.md", b"contents")

    # Act / Assert
    with pytest.raises(UndecryptableObjectError):
        cipher.unseal("memory/impostor.md", sealed)


def test_a_newer_unreadable_object_does_not_overwrite_local_history(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """The recency rule would otherwise hand a corrupt object the win."""
    # Arrange: push, then corrupt the stored object and make it the newer copy.
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    local = home / "sessions" / "abc.jsonl"
    original = local.read_bytes()
    damaged = bytearray(store.objects["sessions/abc.jsonl"])
    damaged[-1] ^= 0xFF
    store.objects["sessions/abc.jsonl"] = bytes(damaged)
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) + timedelta(hours=1)

    # Act / Assert
    gate = resolve_cipher(store, encrypted=True)
    with pytest.raises(UndecryptableObjectError):
        run_sync(
            store,
            direction=SyncDirection.PULL,
            roots=roots,
            cipher=gate.cipher,
            listing=gate.listing,
        )
    assert local.read_bytes() == original


# ── The fail-closed matrix ──────────────────────────────────────────────────


def test_encrypted_machine_refuses_a_store_holding_plaintext() -> None:
    # Arrange
    store = FakeObjectStore()
    store.put_object("memory/old.md", b"readable history")

    # Act / Assert
    with pytest.raises(PlaintextStoreError):
        resolve_cipher(store, encrypted=True)


def test_plaintext_machine_refuses_an_encrypted_store(
    roots: tuple[SyncRoot, ...],
) -> None:
    """The direction that would upload readable history into a sealed store."""
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)

    # Act / Assert
    with pytest.raises(EncryptedStoreError):
        resolve_cipher(store, encrypted=False)


def test_missing_passphrase_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.delenv(REMOTE_SYNC_PASSPHRASE_ENV, raising=False)
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")

    # Act / Assert
    with pytest.raises(MissingPassphraseError):
        resolve_cipher(FakeObjectStore(), encrypted=True)


def test_a_warm_cache_cannot_answer_for_a_different_passphrase(
    roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cached KEK must not stand in for checking the passphrase.

    The cache exists so scrypt is paid once per machine. Keyed on the salt
    alone it answered before the supplied passphrase was looked at, so a wrong
    one opened the store — and unwrapping afterwards does not catch it, because
    the cached KEK unwraps the manifest correctly.
    """
    # Arrange: a real credential file, and cache writes actually enabled — the
    # suite disables them globally, which would make this test prove nothing.
    from config.constants import paths as paths_mod
    from config.secrets import local_file

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    store = FakeObjectStore()
    _encrypted_push(store, roots)

    # Guard: the cache really is warm, so the assertion below has teeth.
    assert local_file.get(REMOTE_SYNC_KEY_CACHE_ENV), "cache never written; test proves nothing"

    # Act / Assert: a wrong passphrase is refused despite the warm cache.
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, "not the right one")
    with pytest.raises(WrongPassphraseError):
        resolve_cipher(store, encrypted=True)


def test_wrong_passphrase_stops_the_run(
    roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, "not the right one")

    # Act / Assert
    with pytest.raises(WrongPassphraseError):
        resolve_cipher(store, encrypted=True)


def test_a_deleted_manifest_never_looks_like_a_plaintext_store(
    roots: tuple[SyncRoot, ...],
) -> None:
    """Both paths must refuse, not guess, when the keys are gone.

    With encryption off the engine would write sealed bytes over local sessions;
    with it on the store reads as plaintext and invites a re-encrypt that has
    nothing to decrypt with.
    """
    # Arrange: a sealed store whose manifest has been removed.
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    del store.objects[MANIFEST_KEY]

    # Act / Assert
    with pytest.raises(ManifestMissingError):
        resolve_cipher(store, encrypted=False)
    with pytest.raises(ManifestMissingError):
        resolve_cipher(store, encrypted=True)


def test_status_reports_the_deleted_manifest_that_sync_refuses(
    home: Path, roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status must not call a store healthy that ``sync`` would refuse.

    With encryption off on this machine the listing alone reads as a plaintext
    store, so status has to run the same gate — otherwise the operator is told
    everything is fine and only learns otherwise mid-sync.
    """
    # Arrange: a sealed store minus its manifest, and encryption off here.
    from config.constants import paths
    from config.constants.filestorage import (
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENCRYPT_ENV,
        REMOTE_SYNC_ENV,
    )
    from infrastructure.filestorage import operations

    store = FakeObjectStore()
    _encrypted_push(store, roots)
    del store.objects[MANIFEST_KEY]

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", home)
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_ENCRYPT_ENV, "0")
    monkeypatch.setattr(operations, "build_object_store", lambda _config: store)

    # Act
    status = operations.get_sync_status()

    # Assert
    assert status.encryption is not None
    assert status.encryption.healthy is False
    assert "manifest" in status.encryption.problem
    with pytest.raises(ManifestMissingError):
        operations.run_remote_sync()


def test_a_genuinely_plaintext_store_still_syncs_unencrypted() -> None:
    """The sealed-payload probe must not refuse an ordinary unencrypted store."""
    # Arrange
    store = FakeObjectStore()
    store.put_object("sessions/a.jsonl", b'{"turn": 1}')

    # Act
    gate = resolve_cipher(store, encrypted=False)

    # Assert
    assert gate.cipher is None


def test_unrelated_objects_do_not_count_as_plaintext_history() -> None:
    """Another tool sharing the prefix must not block adopting encryption."""
    # Arrange
    store = FakeObjectStore()
    store.put_object("some-other-tool/data.bin", b"not ours")

    # Act
    gate = resolve_cipher(store, encrypted=True)

    # Assert
    assert gate.cipher is not None


def test_a_dry_run_writes_no_manifest() -> None:
    # Arrange
    store = FakeObjectStore()

    # Act
    resolve_cipher(store, encrypted=True, dry_run=True)

    # Assert
    assert store.objects == {}


# ── Rotation ────────────────────────────────────────────────────────────────


def test_rotating_the_passphrase_rewrites_no_object(
    roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    before = dict(store.objects)

    # Act
    rotate_passphrase(store, old_passphrase=PASSPHRASE, new_passphrase="a longer new one")

    # Assert: only the manifest changed, and the contents still open.
    assert {k: v for k, v in store.objects.items() if k != MANIFEST_KEY} == {
        k: v for k, v in before.items() if k != MANIFEST_KEY
    }
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, "a longer new one")
    gate = resolve_cipher(store, encrypted=True)
    assert gate.cipher is not None
    assert gate.cipher.unseal("memory/a-fact.md", store.objects["memory/a-fact.md"])


def test_the_old_passphrase_stops_working_after_rotation(
    roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)

    # Act
    rotate_passphrase(store, old_passphrase=PASSPHRASE, new_passphrase="a longer new one")

    # Assert
    monkeypatch.setenv(REMOTE_SYNC_PASSPHRASE_ENV, PASSPHRASE)
    with pytest.raises(WrongPassphraseError):
        resolve_cipher(store, encrypted=True)


def test_reencrypt_adopts_a_store_that_predates_encryption(
    roots: tuple[SyncRoot, ...],
) -> None:
    # Arrange: a store written before this feature existed.
    store = FakeObjectStore()
    store.put_object("memory/a-fact.md", b"remembered\n")
    store.put_object("sessions/abc.jsonl", LEAKED_SECRET.encode())

    # Act
    report = reencrypt(store, passphrase=PASSPHRASE)

    # Assert
    assert report.adopted == 2
    assert LEAKED_SECRET.encode() not in b"".join(store.objects.values())
    gate = resolve_cipher(store, encrypted=True)
    assert gate.cipher is not None


def test_reencrypt_refuses_a_passphrase_that_does_not_open_the_store(
    roots: tuple[SyncRoot, ...],
) -> None:
    """A changed passphrase must stop the run before the manifest is written.

    Minting the new generation cannot fail on its own — wrapping a fresh secret
    checks nothing, and the cipher built afterwards opens that new key while
    silently skipping the old ones. Without an upfront check the run persisted a
    manifest no single passphrase could open in full, then died on the first
    object with an error that read like remote corruption.
    """
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    before = dict(store.objects)

    # Act / Assert
    with pytest.raises(WrongPassphraseError):
        reencrypt(store, passphrase="not the right one")

    # Nothing was written, and the real passphrase still opens every object.
    assert store.objects == before
    gate = resolve_cipher(store, encrypted=True)
    assert gate.cipher is not None
    for key, data in store.objects.items():
        if key != MANIFEST_KEY:
            assert gate.cipher.unseal(key, data)


def test_an_interrupted_reencrypt_leaves_every_object_readable(
    roots: tuple[SyncRoot, ...],
) -> None:
    """The new key must reach the store before anything is sealed under it.

    Sealed first and persisted after, a run that dies mid-loop strands every
    object it already rewrote: their key existed only in the dead process. This
    dies partway on purpose and asserts the store is still wholly readable.
    """
    # Arrange: a store sealed under generation one, then armed to fail.
    store = CrashingStore()
    _encrypted_push(store, roots)
    store.remaining_writes = 2

    # Act
    with pytest.raises(RuntimeError):
        reencrypt(store, passphrase=PASSPHRASE)

    # Assert: a mix of generations, every one of them openable.
    gate = resolve_cipher(store, encrypted=True)
    assert gate.cipher is not None
    sealed = {key: data for key, data in store.objects.items() if key != MANIFEST_KEY}
    assert sealed
    for key, data in sealed.items():
        assert gate.cipher.unseal(key, data)


# ── The manifest is untrusted input ─────────────────────────────────────────


def _manifest_bytes(*, n: int = 131072, r: int = 8, p: int = 1, salt: bytes = b"0" * 16) -> bytes:
    """A syntactically valid manifest with the given KDF shape."""
    return json.dumps(
        {
            "version": 1,
            "active_key_id": "aa" * 16,
            "kdf": {
                "name": "scrypt",
                "n": n,
                "r": r,
                "p": p,
                "salt": base64.b64encode(salt).decode(),
            },
            "wrapped_keys": {"aa" * 16: "AAAA"},
        }
    ).encode()


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("n is not a power of two", {"n": 100_000}),
        ("n would allocate a terabyte", {"n": 2**30}),
        ("n below the supported floor", {"n": 1024}),
        ("n is not even a finite number", {"n": 1e400}),
        ("r past the supported ceiling", {"r": 1 << 20}),
        ("p past the supported ceiling", {"p": 1 << 20}),
        ("salt too short to be one", {"salt": b"x"}),
        ("salt implausibly long", {"salt": b"x" * 4096}),
    ],
)
def test_a_hostile_manifest_cannot_dictate_the_work_factor(
    label: str, kwargs: dict[str, object]
) -> None:
    """The manifest is remote data, so its KDF cost is untrusted.

    scrypt allocates ``128 * n * r`` bytes before any passphrase is checked, so
    an unbounded ``n`` is a memory bomb anyone with write access to the store
    can plant. Malformed values must also surface as this feature's own error
    rather than escaping as a ``cryptography`` ValueError.
    """
    with pytest.raises(RemoteSyncEncryptionError):
        parse_manifest(_manifest_bytes(**kwargs))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("label", "manifest"),
    [
        ("kdf is a list, not an object", {"kdf": []}),
        ("kdf is a bare string", {"kdf": "scrypt"}),
        ("wrapped_keys is a list of ids", {"wrapped_keys": ["aa" * 16]}),
        ("wrapped_keys is null", {"wrapped_keys": None}),
    ],
)
def test_a_manifest_of_the_wrong_json_shape_is_reported_as_damaged(
    label: str, manifest: dict[str, object]
) -> None:
    """A member of the wrong JSON type must not escape as an ``AttributeError``.

    ``kdf.get`` and ``wrapped_keys.items()`` are attribute lookups, so a list or
    string in either slot raised past the parser's own error and out of every
    caller that only expects ``RemoteSyncEncryptionError``.
    """
    payload = json.loads(_manifest_bytes())
    payload.update(manifest)

    with pytest.raises(RemoteSyncEncryptionError):
        parse_manifest(json.dumps(payload).encode())


def test_the_shipped_defaults_stay_inside_the_bounds() -> None:
    """The guard must not reject the parameters opensre itself writes."""
    assert parse_manifest(_manifest_bytes()).params == ScryptParams()


def test_reencrypt_keeps_the_previous_key_in_the_manifest(
    roots: tuple[SyncRoot, ...],
) -> None:
    # Arrange
    store = FakeObjectStore()
    _encrypted_push(store, roots)
    before = load_manifest(store).active_key_id

    # Act
    reencrypt(store, passphrase=PASSPHRASE)

    # Assert
    after = load_manifest(store)
    assert after.active_key_id != before
    assert before in after.wrapped_keys


# ── The engine is unchanged when encryption is off ──────────────────────────


def test_no_cipher_stores_the_file_verbatim(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """The default path must be byte-identical to before this feature."""
    # Arrange
    store = FakeObjectStore()

    # Act
    run_sync(store, direction=SyncDirection.PUSH, roots=roots)

    # Assert
    assert store.objects["sessions/abc.jsonl"] == (home / "sessions" / "abc.jsonl").read_bytes()
