"""Scoped read-only operations over one configured local source tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_READ_LINES = 400
MAX_SEARCH_FILE_BYTES = 1_000_000


class LocalSourceError(ValueError):
    """Expected local-source boundary failure with a stable error code."""


def _root(root_path: str) -> Path:
    try:
        root = Path(root_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalSourceError("source_root_unavailable") from exc
    if not root.is_dir():
        raise LocalSourceError("source_root_unavailable")
    return root


def _target(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path or ".")
    if relative.is_absolute():
        raise LocalSourceError("path_outside_source_root")
    try:
        target = (root / relative).resolve(strict=True)
    except FileNotFoundError as exc:
        raise LocalSourceError("path_not_found") from exc
    except (OSError, RuntimeError) as exc:
        raise LocalSourceError("path_unreadable") from exc
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise LocalSourceError("path_outside_source_root") from exc
    return target


def _relative(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def list_tree(
    root_path: str,
    *,
    path: str = "",
    depth: int = 2,
    limit: int = 200,
) -> dict[str, Any]:
    """List a deterministic, bounded portion of the configured source tree."""
    root = _root(root_path)
    target = _target(root, path)
    if not target.is_dir():
        raise LocalSourceError("path_not_directory")

    effective_depth = min(max(1, depth), 10)
    effective_limit = min(max(1, limit), 1000)
    entries: list[dict[str, str]] = []
    truncated = False

    def visit(directory: Path, level: int) -> None:
        nonlocal truncated
        if truncated or level > effective_depth:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise LocalSourceError("path_unreadable") from exc
        for child in children:
            try:
                resolved = child.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            if len(entries) >= effective_limit:
                truncated = True
                return
            is_directory = resolved.is_dir()
            entries.append(
                {
                    "path": _relative(root, child),
                    "type": "directory" if is_directory else "file",
                }
            )
            if is_directory and not child.is_symlink() and level < effective_depth:
                visit(child, level + 1)

    visit(target, 1)
    return {
        "source": "local_source",
        "available": True,
        "path": _relative(root, target),
        "entries": entries,
        "truncated": truncated,
    }


def search(
    root_path: str,
    *,
    query: str,
    path: str = "",
    file_glob: str = "*",
    limit: int = 100,
) -> dict[str, Any]:
    """Search source text literally, returning bounded relative line matches."""
    if not query:
        raise LocalSourceError("query_required")
    root = _root(root_path)
    target = _target(root, path)
    effective_limit = min(max(1, limit), 1000)
    candidates = [target] if target.is_file() else sorted(target.rglob(file_glob))
    matches: list[dict[str, Any]] = []
    truncated = False
    needle = query.casefold()

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            with resolved.open("r", encoding="utf-8", errors="replace") as source:
                for line_number, line in enumerate(source, start=1):
                    if needle not in line.casefold():
                        continue
                    if len(matches) >= effective_limit:
                        truncated = True
                        break
                    matches.append(
                        {
                            "path": _relative(root, resolved),
                            "line": line_number,
                            "text": line.rstrip("\r\n")[:1000],
                        }
                    )
        except (OSError, RuntimeError, ValueError):
            continue
        if truncated:
            break

    return {
        "source": "local_source",
        "available": True,
        "query": query,
        "matches": matches,
        "truncated": truncated,
    }


def read_file(
    root_path: str,
    *,
    path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> dict[str, Any]:
    """Read at most ``MAX_READ_LINES`` from one scoped source file."""
    root = _root(root_path)
    target = _target(root, path)
    if not target.is_file():
        raise LocalSourceError("path_not_file")

    effective_start = max(1, start_line)
    explicit_end = end_line if end_line >= effective_start else 0
    requested_end = explicit_end or effective_start + MAX_READ_LINES - 1
    effective_end = min(requested_end, effective_start + MAX_READ_LINES - 1)
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise LocalSourceError("path_unreadable") from exc
    selected = lines[effective_start - 1 : effective_end]
    actual_end = effective_start + len(selected) - 1 if selected else effective_start - 1
    truncated = (not explicit_end and effective_end < len(lines)) or requested_end > effective_end
    return {
        "source": "local_source",
        "available": True,
        "path": _relative(root, target),
        "start_line": effective_start,
        "end_line": actual_end,
        "content": "\n".join(selected),
        "truncated": truncated,
    }


__all__ = ["LocalSourceError", "list_tree", "read_file", "search"]
