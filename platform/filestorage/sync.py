"""Mirror conversation history and memory between a laptop and a bucket.

Additive on both sides: a file present in one place and missing in the other is
copied, never deleted. When both sides changed, the more recently written one
wins. Nothing here removes a session or a memory, so a stale second machine
cannot erase work done on the first.

Sessions are append-mostly JSONL and memory files are small markdown, so whole
objects are transferred rather than ranges.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from platform.filestorage.errors import UnsyncablePathError
from platform.filestorage.ports import ObjectStore, RemoteObject
from platform.filestorage.scope import SyncRoot, is_syncable, syncable_roots

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    """What one sync moved, for the CLI to print and tests to assert on."""

    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def changed(self) -> int:
        return len(self.uploaded) + len(self.downloaded)


def file_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_files(root: SyncRoot) -> list[Path]:
    if not root.path.is_dir():
        return []
    return sorted(p for p in root.path.rglob("*") if p.is_file())


def _relative_key(root: SyncRoot, path: Path) -> str:
    return f"{root.name}/{path.relative_to(root.path).as_posix()}"


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def push(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
    report: SyncReport | None = None,
) -> SyncReport:
    """Upload local files whose contents differ from the bucket."""
    roots = roots if roots is not None else syncable_roots()
    result = report if report is not None else SyncReport()
    remote = {obj.key: obj for obj in store.list_objects("")}

    for root in roots:
        for path in _local_files(root):
            if not is_syncable(path, roots=roots):
                # Reaching here means a root pointed somewhere it should not.
                raise UnsyncablePathError(f"refusing to upload {path}")
            key = _relative_key(root, path)
            data = path.read_bytes()
            digest = file_digest(data)
            existing = remote.get(key)
            if existing is not None and existing.digest == digest:
                result.skipped += 1
                continue
            store.put_object(key, data, digest=digest)
            result.uploaded.append(key)
    return result


def pull(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
    report: SyncReport | None = None,
) -> SyncReport:
    """Download bucket objects missing locally, or newer than the local copy."""
    roots = roots if roots is not None else syncable_roots()
    result = report if report is not None else SyncReport()
    by_name = {root.name: root for root in roots}

    for obj in store.list_objects(""):
        target = _local_path_for(obj, by_name)
        if target is None:
            continue
        if not _should_download(obj, target):
            result.skipped += 1
            continue
        data = store.get_object(obj.key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
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
    local = target.read_bytes()
    if obj.digest and obj.digest == file_digest(local):
        return False
    # Both sides changed: the more recent write wins.
    return obj.last_modified > _modified_at(target)


def sync(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
) -> SyncReport:
    """Pull first, then push, so a local edit made offline still wins."""
    report = SyncReport()
    pull(store, roots=roots, report=report)
    push(store, roots=roots, report=report)
    return report


__all__ = ["SyncReport", "file_digest", "pull", "push", "sync"]
