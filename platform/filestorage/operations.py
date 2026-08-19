"""Remote-sync actions a surface invokes.

CLI (``opensre remote-sync``), interactive shell (``/remote-sync``), and
gateway clients all call here, so none of them re-implements config loading,
store building, or engine ordering. Surfaces own only their own I/O.

**Stateless:** no cached config, ObjectStore, or report. Every call re-reads
env/settings, resolves roots for the current scope, and builds a fresh store.
**Thread-safe:** safe to call concurrently from many turns; results are newly
allocated and not shared. Two syncs of the same roots can still race at the
filesystem / object-store layer (last writer wins) — that is external I/O.

Roots come from ``sessions_dir()`` / ``get_memory_dir()``, so the active
principal scope (laptop home vs org user tree) is already applied.

User-facing wording lives in :mod:`platform.filestorage.messages`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from config.scope_context import current_scope
from platform.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from platform.filestorage.encryption.manifest import manifest_in_listing
from platform.filestorage.encryption.resolver import resolve_cipher
from platform.filestorage.encryption.rotation import (
    ReencryptReport,
    reencrypt,
    rotate_passphrase,
)
from platform.filestorage.engine import (
    ProgressCallback,
    SyncReport,
    local_files,
    relative_key,
    resolve_direction,
    run_sync,
)
from platform.filestorage.enums import SyncDirection, SyncRootName
from platform.filestorage.errors import OrgScopeNotSupportedError, RemoteSyncEncryptionError
from platform.filestorage.exclusions import NO_EXCLUSIONS, ExclusionRules
from platform.filestorage.exposure import PublicAccessStatus
from platform.filestorage.providers import (
    build_object_store,
    check_bucket_exposure,
    max_parallel_uploads_for_provider,
)
from platform.filestorage.syncable import SyncRoot, syncable_roots

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncRootStatus:
    """One mirrored root as shown to the operator.

    ``excluded`` is how many files under this root the configured patterns
    currently hold back. Reported per root because a count is the only way to
    tell a pattern that matched from one that was mistyped.
    """

    name: SyncRootName | str
    path: Path
    exists: bool
    excluded: int = 0


@dataclass(frozen=True)
class EncryptionStatus:
    """Whether contents are sealed, and whether this machine can open them.

    ``problem`` carries the reason a sync would be refused, so ``status``
    reports a missing passphrase or a plaintext/encrypted mismatch *before* the
    operator discovers it mid-sync. Empty when nothing is wrong.
    """

    configured: bool
    store_encrypted: bool
    key_available: bool = False
    problem: str = ""

    @property
    def healthy(self) -> bool:
        return not self.problem


@dataclass(frozen=True)
class SyncStatus:
    """Whether sync is on and what would move — shared across all surfaces.

    ``exposure`` is ``None`` when sync is off (nothing to check) and a
    :class:`~platform.filestorage.exposure.PublicAccessStatus` otherwise —
    always present, since :func:`~platform.filestorage.providers.check_bucket_exposure`
    itself degrades to ``UNKNOWN`` instead of raising.

    ``encryption`` is ``None`` when sync is off, for the same reason.
    """

    config: RemoteSyncConfig | None
    roots: tuple[SyncRootStatus, ...]
    exposure: PublicAccessStatus | None = None
    encryption: EncryptionStatus | None = None

    @property
    def enabled(self) -> bool:
        return self.config is not None

    @property
    def exclusions(self) -> ExclusionRules:
        """Patterns in force, empty when sync is off or none are configured."""
        return self.config.exclude if self.config is not None else NO_EXCLUSIONS


def _owned_report(report: SyncReport) -> SyncReport:
    """Caller-owned copy so concurrent formatters cannot see later mutation."""
    return SyncReport(
        uploaded=list(report.uploaded),
        downloaded=list(report.downloaded),
        kept_remote=list(report.kept_remote),
        skipped=report.skipped,
        uploaded_bytes=report.uploaded_bytes,
        downloaded_bytes=report.downloaded_bytes,
        excluded=set(report.excluded),
    )


def _refuse_org_scoped_turn() -> None:
    """Fail closed when the caller is an organization member, not a laptop user.

    Object keys carry no principal or actor, so every member of an organization
    would write and read the same keys.
    """
    scope = current_scope()
    if scope is not None and scope.principal.kind == "org":
        raise OrgScopeNotSupportedError(
            "Remote sync mirrors a personal machine. This conversation belongs to "
            "an organization, whose history already persists in the shared context "
            "root, so nothing is mirrored."
        )


def _root_status(root: SyncRoot, exclusions: ExclusionRules) -> SyncRootStatus:
    """Describe one root, counting held-back files only when asked to.

    Counting walks the whole root, so an installation with no patterns — the
    default — pays nothing for a feature it is not using.
    """
    excluded = (
        exclusions.matching(relative_key(root, path) for path in local_files(root))
        if exclusions
        else 0
    )
    return SyncRootStatus(
        name=root.name,
        path=root.path,
        exists=root.path.is_dir(),
        excluded=excluded,
    )


def _encryption_status(config: RemoteSyncConfig) -> EncryptionStatus:
    """Report what a sync would decide about encryption, without doing one.

    Runs the same gate the transfer runs, so ``status`` and ``sync`` can never
    disagree about whether this machine may talk to this store. Every failure
    is reported rather than raised: status exists to explain a problem, and one
    that ends in a traceback explains less than a line saying what is wrong.
    """
    try:
        store = build_object_store(config)
        listing = store.list_objects("")
        store_encrypted = manifest_in_listing(listing)
        if not config.encrypted:
            problem = (
                "this store is encrypted but encryption is off on this machine; "
                "a sync would be refused"
                if store_encrypted
                else ""
            )
            return EncryptionStatus(
                configured=False, store_encrypted=store_encrypted, problem=problem
            )
        # Resolving the cipher is what proves the passphrase opens the store.
        resolve_cipher(store, encrypted=True, dry_run=True)
        return EncryptionStatus(
            configured=True, store_encrypted=store_encrypted, key_available=True
        )
    except RemoteSyncEncryptionError as exc:
        # Our own wording, written for an operator to act on, so it is shown.
        return EncryptionStatus(configured=config.encrypted, store_encrypted=True, problem=str(exc))
    except Exception as exc:
        # Status exists to describe problems; one that ends in a traceback
        # describes nothing. Broad on purpose — an unreachable store, a
        # rejected credential, or a registered provider that returns something
        # unusable all end here, and none of them may take the command down.
        # Only the exception's type is reported: this line reaches chat sinks.
        logger.warning("[remote-sync] encryption status check failed", exc_info=True)
        return EncryptionStatus(
            configured=config.encrypted,
            store_encrypted=False,
            problem=f"could not check the store's encryption ({type(exc).__name__})",
        )


def get_sync_status() -> SyncStatus:
    """Load config, resolve scoped roots, and check the store's access and keys.

    Makes network calls only when sync is on: the exposure check (if the
    provider registered a checker; see
    :func:`~platform.filestorage.providers.check_bucket_exposure`) and one
    listing for the encryption check.
    """
    _refuse_org_scoped_turn()
    config = load_remote_sync_config()
    exclusions = config.exclude if config is not None else NO_EXCLUSIONS
    roots = tuple(_root_status(root, exclusions) for root in syncable_roots())
    exposure = check_bucket_exposure(config) if config is not None else None
    encryption = _encryption_status(config) if config is not None else None
    return SyncStatus(config=config, roots=roots, exposure=exposure, encryption=encryption)


def run_remote_sync(
    *,
    pull_only: bool = False,
    push_only: bool = False,
    direction: SyncDirection | None = None,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
) -> SyncReport | None:
    """Pull/push for the current scope. ``None`` when sync is disabled.

    Builds a new ObjectStore per call. Returns a caller-owned report snapshot.
    Prefer ``direction=`` when the caller already has a :class:`SyncDirection`;
    the boolean flags remain for CLI/slash adapters. ``dry_run`` previews the
    plan without uploading, downloading, or writing anything locally.
    ``on_progress``, when given, is called once per key evaluated — the
    single place CLI and slash both get live progress from, so neither
    re-derives it. See :class:`platform.filestorage.engine.SyncProgress`.

    Encryption is settled before anything moves: a store and a machine that
    disagree about it fail here, with nothing uploaded or written.
    """
    _refuse_org_scoped_turn()
    resolved = (
        direction
        if direction is not None
        else resolve_direction(pull_only=pull_only, push_only=push_only)
    )
    config = load_remote_sync_config()
    if config is None:
        return None
    roots = syncable_roots()
    store = build_object_store(config)
    # Fails closed on any mismatch, and hands back the listing it had to fetch
    # to decide, so the sync below does not list the store a second time.
    gate = resolve_cipher(store, encrypted=config.encrypted, dry_run=dry_run)
    return _owned_report(
        run_sync(
            store,
            direction=resolved,
            roots=roots,
            exclusions=config.exclude,
            dry_run=dry_run,
            on_progress=on_progress,
            # The backend, not the engine, knows how hard it can be pushed.
            max_parallel_uploads=max_parallel_uploads_for_provider(config.provider),
            cipher=gate.cipher,
            listing=gate.listing,
        )
    )


def rotate_remote_sync_passphrase(*, old_passphrase: str, new_passphrase: str) -> None:
    """Re-wrap the store's keys under a new passphrase. Rewrites no object.

    Takes effect immediately and everywhere: the old passphrase stops opening
    the manifest, so no machine can derive the content keys from it again.
    """
    _refuse_org_scoped_turn()
    config = _encrypted_config()
    rotate_passphrase(
        build_object_store(config),
        old_passphrase=old_passphrase,
        new_passphrase=new_passphrase,
    )


def reencrypt_remote_store(*, passphrase: str) -> ReencryptReport:
    """Seal every stored object under a fresh content key.

    The migration path for a store that predates encryption, and the only way
    to replace the content key itself. Costs a full re-upload; prefer
    :func:`rotate_remote_sync_passphrase` when the passphrase is what changed.
    """
    _refuse_org_scoped_turn()
    config = _encrypted_config()
    return reencrypt(build_object_store(config), passphrase=passphrase)


def _encrypted_config() -> RemoteSyncConfig:
    """Loaded config for a key operation, refusing when sync or encryption is off."""
    config = load_remote_sync_config()
    if config is None:
        raise RemoteSyncEncryptionError(
            "Remote sync is off on this machine.\n"
            "\n"
            "  Set it up first:  `opensre remote-sync setup` or `export OPENSRE_REMOTE_SYNC=1`"
        )
    if not config.encrypted:
        raise RemoteSyncEncryptionError(
            "Encryption is off on this machine, so there is no key to change.\n"
            "\n"
            "  Turn it on first:  opensre remote-sync setup"
        )
    return config


__all__ = [
    "EncryptionStatus",
    "SyncRootStatus",
    "SyncStatus",
    "get_sync_status",
    "reencrypt_remote_store",
    "rotate_remote_sync_passphrase",
    "run_remote_sync",
]
