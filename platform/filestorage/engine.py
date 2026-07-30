"""Mirror conversation history and memory between a laptop and a bucket.

Additive on both sides: a file present in one place and missing in the other is
copied, never deleted. When both sides changed, the more recently written one
wins — in both directions, so a push from a laptop that has been offline cannot
replace newer work in the bucket any more than a stale pull can overwrite a
newer local edit. Nothing here removes a session or a memory.

Sessions are append-mostly JSONL and memory files are small markdown, so whole
objects are transferred rather than ranges. Downloads land through a temporary
file and an atomic rename, matching how every local store writes, so an
interrupted pull cannot leave a half-written session behind.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from platform.filestorage.enums import SyncDirection
from platform.filestorage.errors import RemoteSyncConfigError, UnsyncablePathError
from platform.filestorage.ports import ObjectStore, RemoteObject
from platform.filestorage.syncable import SyncRoot, resolved_roots, syncable_roots

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    """What one sync moved, for the surfaces to print and tests to assert on."""

    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    #: Local files left alone because the bucket held a newer copy. A full sync
    #: resolves these by pulling first; a push-only run cannot.
    kept_remote: list[str] = field(default_factory=list)
    skipped: int = 0
    uploaded_bytes: int = 0
    downloaded_bytes: int = 0

    @property
    def changed(self) -> int:
        return len(self.uploaded) + len(self.downloaded)

    @property
    def total_bytes(self) -> int:
        return self.uploaded_bytes + self.downloaded_bytes


def content_tag(data: bytes) -> str:
    """Content tag comparable with an S3 ETag.

    A change detector, not a security control: it answers "are these the same
    bytes the bucket already holds", and S3 defines that tag as MD5.
    """
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def comparable_etag(obj: RemoteObject) -> str:
    """The object's tag when it can be compared, otherwise empty.

    A multipart upload returns a compound tag (``<hash>-<parts>``) that is not
    the MD5 of the content, so it is treated as no tag at all.
    """
    tag = obj.etag.strip('"')
    return "" if not tag or "-" in tag else tag


def resolve_direction(*, pull_only: bool, push_only: bool) -> SyncDirection:
    """Turn the two surface flags into one direction, rejecting both at once."""
    if pull_only and push_only:
        raise RemoteSyncConfigError("choose one of --pull-only or --push-only, not both")
    if pull_only:
        return SyncDirection.PULL
    if push_only:
        return SyncDirection.PUSH
    return SyncDirection.BOTH


def _local_files(root: SyncRoot) -> list[Path]:
    if not root.path.is_dir():
        return []
    return sorted(p for p in root.path.rglob("*") if p.is_file())


def _relative_key(root: SyncRoot, path: Path) -> str:
    return f"{root.name}/{path.relative_to(root.path).as_posix()}"


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _write_atomically(target: Path, data: bytes) -> None:
    """Write through a temporary file in the same directory, then rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def push(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
    report: SyncReport | None = None,
    remote: list[RemoteObject] | None = None,
) -> SyncReport:
    """Upload local files whose contents differ from the bucket."""
    roots = roots if roots is not None else syncable_roots()
    result = report if report is not None else SyncReport()
    listing = remote if remote is not None else store.list_objects("")
    by_key = {obj.key: obj for obj in listing}
    # Resolve each root once rather than per file: this loop touches every
    # session and memory file on the machine.
    allowed = resolved_roots(roots)

    # Check every candidate before uploading any of them. A denied file found
    # halfway through must not leave earlier files already in the store.
    planned: list[tuple[SyncRoot, Path]] = []
    for root in roots:
        for path in _local_files(root):
            if not allowed.contains(path):
                # Reaching here means a root pointed somewhere it should not.
                raise UnsyncablePathError(f"refusing to upload {path}")
            planned.append((root, path))

    for root, path in planned:
        key = _relative_key(root, path)
        data = path.read_bytes()
        existing = by_key.get(key)
        if existing is not None:
            if comparable_etag(existing) == content_tag(data):
                result.skipped += 1
                continue
            if existing.last_modified > _modified_at(path):
                # The store holds the more recent write, so uploading would
                # destroy it. Same rule pull applies in the other direction.
                result.kept_remote.append(key)
                continue
        store.put_object(key, data)
        result.uploaded.append(key)
        result.uploaded_bytes += len(data)
    return result


def pull(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
    report: SyncReport | None = None,
    remote: list[RemoteObject] | None = None,
) -> SyncReport:
    """Download bucket objects missing locally, or newer than the local copy."""
    roots = roots if roots is not None else syncable_roots()
    result = report if report is not None else SyncReport()
    by_name = {root.name: root for root in roots}

    for obj in remote if remote is not None else store.list_objects(""):
        target = _local_path_for(obj, by_name)
        if target is None:
            continue
        if not _should_download(obj, target):
            result.skipped += 1
            continue
        data = store.get_object(obj.key)
        result.downloaded_bytes += len(data)
        _write_atomically(target, data)
        result.downloaded.append(obj.key)
    return result


def _local_path_for(obj: RemoteObject, by_name: dict[str, SyncRoot]) -> Path | None:
    """Local file for one object key, or None when the key is not ours."""
    head, _, tail = obj.key.partition("/")
    root = by_name.get(head)
    if root is None or not tail:
        logger.debug("[remote-sync] ignoring unrecognised key %s", obj.key)
        return None
    # A key may not climb out of its root.
    candidate = (root.path / tail).resolve()
    if root.path.resolve() not in candidate.parents:
        logger.warning("[remote-sync] refusing key that escapes its root")
        return None
    return candidate


def _should_download(obj: RemoteObject, target: Path) -> bool:
    if not target.exists():
        return True
    tag = comparable_etag(obj)
    if tag and tag == content_tag(target.read_bytes()):
        return False
    # Both sides changed: the more recent write wins.
    return obj.last_modified > _modified_at(target)


def run_sync(
    store: ObjectStore,
    *,
    direction: SyncDirection = SyncDirection.BOTH,
    roots: tuple[SyncRoot, ...] | None = None,
) -> SyncReport:
    """Move files in ``direction``. Both ways pulls first, so an offline edit wins.

    The listing is fetched once and shared: a pull changes local files, never
    the bucket, so the push half can reuse it.
    """
    report = SyncReport()
    listing = store.list_objects("")
    if direction is not SyncDirection.PUSH:
        pull(store, roots=roots, report=report, remote=listing)
    if direction is not SyncDirection.PULL:
        push(store, roots=roots, report=report, remote=listing)
    return report


__all__ = [
    "SyncDirection",
    "SyncReport",
    "comparable_etag",
    "content_tag",
    "pull",
    "push",
    "resolve_direction",
    "run_sync",
]
