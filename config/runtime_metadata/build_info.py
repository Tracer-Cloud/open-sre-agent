"""Git build-marker detection via filesystem reads (no subprocess).

Resolves the enclosing checkout's git layout — including linked worktrees,
submodule pointer files, and packed refs — and renders a human-readable build
marker: ``""`` for installed wheels, ``dev, <tag> @ <sha>`` for checkouts.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_RELEASE_TAG_PATTERN = re.compile(r"^v\d+\.\d+(\.\d+){2,}$")


def resolve_gitdir(candidate: Path) -> Path | None:
    """Return the git directory for ``candidate`` (``.git``), or ``None``.

    Handles both a normal checkout (``.git`` is a directory) and a linked
    worktree / submodule (``.git`` is a file with a ``gitdir: <path>`` line).
    """
    if candidate.is_dir():
        return candidate
    if not candidate.is_file():
        return None
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("gitdir:"):
            continue
        target = Path(stripped[len("gitdir:") :].strip())
        if not target.is_absolute():
            target = (candidate.parent / target).resolve()
        return target if target.is_dir() else None
    return None


@dataclass(frozen=True)
class GitLayout:
    """Per-worktree gitdir plus the shared common gitdir.

    In a standard checkout the two are the same directory. In a linked worktree
    (``git worktree add``), ``HEAD`` is per-worktree but ``refs/``, ``packed-refs``,
    and tags live in the primary repo's gitdir named by the worktree's
    ``commondir`` marker file.
    """

    gitdir: Path
    commondir: Path


def resolve_commondir(gitdir: Path) -> Path:
    """Return the shared common gitdir for ``gitdir``.

    Standard checkouts have no ``commondir`` marker; the gitdir is its own
    common dir. Linked worktrees carry a ``commondir`` file with a path
    (relative to the per-worktree gitdir) to the primary repo's gitdir.
    """
    marker = gitdir / "commondir"
    if not marker.is_file():
        return gitdir
    try:
        content = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    if not content:
        return gitdir
    target = Path(content)
    if not target.is_absolute():
        target = (gitdir / target).resolve()
    return target if target.is_dir() else gitdir


def find_git_layout() -> GitLayout | None:
    """Walk up from this file to the enclosing repo's git layout."""
    here = Path(__file__).resolve().parent
    while here.parent != here:
        gitdir = resolve_gitdir(here / ".git")
        if gitdir is not None:
            return GitLayout(gitdir=gitdir, commondir=resolve_commondir(gitdir))
        here = here.parent
    return None


def read_packed_refs(commondir: Path) -> dict[str, str]:
    """Parse ``<commondir>/packed-refs`` into a ``{ref_name: sha}`` map.

    After ``git pack-refs`` the loose files under ``refs/`` disappear and both
    branch heads and tag refs live only here. Peeled tag lines (``^<sha>``) are
    ignored: the non-peeled line already holds the tag object's sha which is
    enough for a build marker.
    """
    packed = commondir / "packed-refs"
    if not packed.is_file():
        return {}
    refs: dict[str, str] = {}
    try:
        content = packed.read_text(encoding="utf-8")
    except OSError:
        return {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if sha and name:
            refs[name] = sha
    return refs


def read_ref_sha(layout: GitLayout, ref_name: str) -> str | None:
    """Resolve ``ref_name`` (e.g. ``refs/heads/main``) via loose files + packed-refs.

    Per-worktree refs (bisect/HEAD-like) may live under the worktree gitdir,
    so it's tried first; branches and tags live in the commondir.
    """
    for base in (layout.gitdir, layout.commondir):
        loose = base / ref_name
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
    return read_packed_refs(layout.commondir).get(ref_name)


_HEAD_SYMREF_PREFIX = "ref: "


def _read_head(layout: GitLayout) -> str | None:
    """Raw ``HEAD`` contents — a ``ref: refs/…`` symref or a detached sha — or ``None``."""
    head_file = layout.gitdir / "HEAD"
    if not head_file.is_file():
        return None
    return head_file.read_text(encoding="utf-8").strip() or None


def read_git_head_full_sha(layout: GitLayout) -> str | None:
    """Full SHA the working tree currently points at, or ``None``."""
    head = _read_head(layout)
    if head is None:
        return None
    if not head.startswith(_HEAD_SYMREF_PREFIX):
        return head  # detached HEAD: HEAD is the full sha
    return read_ref_sha(layout, head.removeprefix(_HEAD_SYMREF_PREFIX).strip())


def read_git_head_sha(layout: GitLayout) -> str | None:
    """Short SHA the working tree currently points at, or ``None``."""
    sha = read_git_head_full_sha(layout)
    return sha[:7] if sha else None


_COMMITTER_LINE_PREFIX = b"committer "


def _parse_committer_date(commit_object: bytes) -> datetime | None:
    """UTC datetime from a commit object's ``committer`` line, or ``None``.

    The line ends ``… <unix_seconds> <tz>``; the trailing two tokens are the
    epoch timestamp and the author's zone. UTC is used for a zone-independent,
    reproducible build date.
    """
    for line in commit_object.split(b"\n"):
        if not line.startswith(_COMMITTER_LINE_PREFIX):
            continue
        fields = line.split(b" ")
        try:
            return datetime.fromtimestamp(int(fields[-2]), tz=UTC)
        except (IndexError, ValueError):
            return None
    return None


_PACK_IDX_MAGIC = b"\xfftOc"
_PACK_IDX_V2 = 2
_PACK_OBJ_COMMIT = 1
_PACK_LARGE_OFFSET_FLAG = 0x80000000
_VARINT_CONTINUE = 0x80


def _read_loose_commit_object(commondir: Path, sha: str) -> bytes | None:
    """Inflated loose object bytes for *sha*, or ``None`` when not stored loose."""
    loose_object = commondir / "objects" / sha[:2] / sha[2:]
    if not loose_object.is_file():
        return None
    try:
        return zlib.decompress(loose_object.read_bytes())
    except (OSError, zlib.error):
        return None


def _find_object_offset_in_pack_idx(idx_path: Path, sha_bytes: bytes) -> int | None:
    """Byte offset of *sha_bytes* within the paired pack, via its v2 idx, or ``None``."""
    try:
        data = idx_path.read_bytes()
    except OSError:
        return None
    if data[:4] != _PACK_IDX_MAGIC or int.from_bytes(data[4:8], "big") != _PACK_IDX_V2:
        return None  # only the modern idx v2 layout is parsed
    fanout = 8
    sha_table = fanout + 256 * 4
    count = int.from_bytes(data[fanout + 255 * 4 : sha_table], "big")
    first = sha_bytes[0]
    lo = int.from_bytes(data[fanout + (first - 1) * 4 : fanout + first * 4], "big") if first else 0
    hi = int.from_bytes(data[fanout + first * 4 : fanout + (first + 1) * 4], "big")
    index = next(
        (i for i in range(lo, hi) if data[sha_table + i * 20 : sha_table + (i + 1) * 20] == sha_bytes),
        None,
    )
    if index is None:
        return None
    small_offsets = sha_table + count * 20 + count * 4  # after the sha and crc32 tables
    packed = int.from_bytes(data[small_offsets + index * 4 : small_offsets + (index + 1) * 4], "big")
    if not packed & _PACK_LARGE_OFFSET_FLAG:
        return packed
    large = small_offsets + count * 4 + (packed & ~_PACK_LARGE_OFFSET_FLAG) * 8
    return int.from_bytes(data[large : large + 8], "big")


def _read_pack_commit_at(pack_path: Path, offset: int) -> bytes | None:
    """Inflated bytes of the object at *offset* when it is a base commit, else ``None``.

    Deltified objects (their type is ofs/ref-delta, not commit) are skipped —
    reconstructing them needs base resolution, and a tip commit is virtually
    never stored as a delta.
    """
    try:
        with pack_path.open("rb") as pack:
            pack.seek(offset)
            header = pack.read(1)
            if not header:
                return None
            obj_type = (header[0] >> 4) & 0x7
            while header[0] & _VARINT_CONTINUE:  # consume the size varint header
                header = pack.read(1)
                if not header:
                    return None
            if obj_type != _PACK_OBJ_COMMIT:
                return None
            stream = pack.read()  # zlib stops at the object's stream end
    except OSError:
        return None
    try:
        return zlib.decompress(stream)
    except zlib.error:
        return None


def _read_packed_commit_object(commondir: Path, sha: str) -> bytes | None:
    """Inflated bytes of *sha* from any pack that holds it as a base commit."""
    pack_dir = commondir / "objects" / "pack"
    if not pack_dir.is_dir():
        return None
    try:
        sha_bytes = bytes.fromhex(sha)
    except ValueError:
        return None
    for idx_path in sorted(pack_dir.glob("*.idx")):
        offset = _find_object_offset_in_pack_idx(idx_path, sha_bytes)
        if offset is None:
            continue
        commit = _read_pack_commit_at(idx_path.with_suffix(".pack"), offset)
        if commit is not None:
            return commit
    return None


def read_git_head_commit_date(layout: GitLayout) -> datetime | None:
    """UTC date of the HEAD commit, read from its git object, or ``None``.

    Reproducible per commit — the committer timestamp is baked into the object,
    unlike wall-clock time — and storage-independent: the object is read whether
    it is loose or packed, so packing (``git gc``) does not change the build
    identity. Returns ``None`` only when the object is genuinely unreadable (a
    full 40-char sha is required to address a pack), so callers fall back to a
    date-less identity rather than a date that drifts by run.
    """
    sha = read_git_head_full_sha(layout)
    if sha is None or len(sha) != 40:
        return None
    commit_object = _read_loose_commit_object(layout.commondir, sha) or _read_packed_commit_object(
        layout.commondir, sha
    )
    return _parse_committer_date(commit_object) if commit_object else None


def release_tag_sort_key(name: str) -> tuple[int, ...] | None:
    """Numeric tuple for a ``v0.1.YYYY.M.D`` tag; ``None`` if not all-numeric.

    Numeric sort so ``v0.1.2026.10.1`` outranks ``v0.1.2026.9.30`` — a
    lexicographic sort would pick the older tag because ``'9' > '1'`` as ASCII.
    """
    parts = name.removeprefix("v").split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def iter_release_tag_names(commondir: Path) -> set[str]:
    """Release tag names, from loose refs and from ``packed-refs`` combined."""
    names: set[str] = set()
    tags_dir = commondir / "refs" / "tags"
    if tags_dir.is_dir():
        names.update(entry.name for entry in tags_dir.iterdir())
    for ref_name in read_packed_refs(commondir):
        if ref_name.startswith("refs/tags/"):
            names.add(ref_name[len("refs/tags/") :])
    return names


def read_latest_release_tag(commondir: Path) -> str | None:
    """Highest release tag (loose + packed) by numeric ordering."""
    ranked: list[tuple[tuple[int, ...], str]] = []
    for name in iter_release_tag_names(commondir):
        if not _RELEASE_TAG_PATTERN.match(name):
            continue
        key = release_tag_sort_key(name)
        if key is not None:
            ranked.append((key, name))
    if not ranked:
        return None
    return max(ranked)[1]


def detect_build_info() -> str:
    """Human-readable build marker: ``""`` for wheels, ``dev, <tag> @ <sha>`` for checkouts."""
    layout = find_git_layout()
    if layout is None:
        return ""
    tag = read_latest_release_tag(layout.commondir)
    sha = read_git_head_sha(layout)
    if tag and sha:
        return f"dev, {tag} @ {sha}"
    if tag:
        return f"dev, {tag}"
    if sha:
        return f"dev, @ {sha}"
    return "dev"


__all__ = [
    "GitLayout",
    "detect_build_info",
    "find_git_layout",
    "iter_release_tag_names",
    "read_git_head_commit_date",
    "read_git_head_full_sha",
    "read_git_head_sha",
    "read_latest_release_tag",
    "read_packed_refs",
    "read_ref_sha",
    "release_tag_sort_key",
    "resolve_commondir",
    "resolve_gitdir",
]
