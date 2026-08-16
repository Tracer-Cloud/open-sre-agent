"""Scoped read-only operations over one configured local source tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_READ_LINES = 400
MAX_READ_BYTES = 64_000
READ_CHUNK_CHARS = 8_192
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


def _trim_line_ending(piece: str) -> str:
    return piece[:-2] if piece.endswith("\r\n") else piece.rstrip("\n")


def _append_bounded_piece(
    selected: list[str],
    *,
    piece: str,
    content_bytes: int,
) -> tuple[int, bool]:
    separator_bytes = 1 if selected else 0
    remaining_bytes = MAX_READ_BYTES - content_bytes - separator_bytes
    if remaining_bytes <= 0:
        return content_bytes, True

    piece_bytes = piece.encode("utf-8")
    if len(piece_bytes) > remaining_bytes:
        selected.append(piece_bytes[:remaining_bytes].decode("utf-8", errors="ignore"))
        return MAX_READ_BYTES, True

    selected.append(piece)
    return content_bytes + separator_bytes + len(piece_bytes), False


def _read_selected_lines(
    target: Path,
    *,
    effective_start: int,
    effective_end: int,
    explicit_end: int,
    initial_truncated_by: str | None,
) -> tuple[list[str], str | None]:
    selected: list[str] = []
    content_bytes = 0
    truncated_by = initial_truncated_by
    line_number = 1
    pending_selected_index: int | None = None

    with target.open("r", encoding="utf-8", errors="replace") as source:
        while True:
            if line_number > effective_end:
                raw_piece = source.readline(READ_CHUNK_CHARS)
                if raw_piece != "" and not explicit_end:
                    truncated_by = "line_limit"
                break

            raw_piece = source.readline(READ_CHUNK_CHARS)
            if raw_piece == "":
                break
            line_finished = raw_piece.endswith("\n")
            selected_line = effective_start <= line_number <= effective_end

            if selected_line:
                piece = _trim_line_ending(raw_piece) if line_finished else raw_piece
                if pending_selected_index is None:
                    content_bytes, hit_byte_limit = _append_bounded_piece(
                        selected,
                        piece=piece,
                        content_bytes=content_bytes,
                    )
                    pending_selected_index = len(selected) - 1
                else:
                    piece_bytes = piece.encode("utf-8")
                    remaining_bytes = MAX_READ_BYTES - content_bytes
                    if len(piece_bytes) > remaining_bytes:
                        selected[pending_selected_index] += piece_bytes[
                            :remaining_bytes
                        ].decode("utf-8", errors="ignore")
                        content_bytes = MAX_READ_BYTES
                        hit_byte_limit = True
                    else:
                        selected[pending_selected_index] += piece
                        content_bytes += len(piece_bytes)
                        hit_byte_limit = False
                if hit_byte_limit:
                    truncated_by = "byte_limit"
                    break

            if line_finished:
                if (
                    selected_line
                    and pending_selected_index is not None
                    and selected[pending_selected_index].endswith("\r")
                ):
                    selected[pending_selected_index] = selected[pending_selected_index][:-1]
                    content_bytes -= 1
                pending_selected_index = None
                line_number += 1

    return selected, truncated_by


def read_file(
    root_path: str,
    *,
    path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> dict[str, Any]:
    """Read a bounded slice from one scoped source file."""
    root = _root(root_path)
    target = _target(root, path)
    if not target.is_file():
        raise LocalSourceError("path_not_file")

    effective_start = max(1, start_line)
    explicit_end = end_line if end_line >= effective_start else 0
    requested_end = explicit_end or effective_start + MAX_READ_LINES - 1
    effective_end = min(requested_end, effective_start + MAX_READ_LINES - 1)
    truncated_by: str | None = "line_limit" if requested_end > effective_end else None

    try:
        selected, truncated_by = _read_selected_lines(
            target,
            effective_start=effective_start,
            effective_end=effective_end,
            explicit_end=explicit_end,
            initial_truncated_by=truncated_by,
        )
    except OSError as exc:
        raise LocalSourceError("path_unreadable") from exc

    actual_end = effective_start + len(selected) - 1 if selected else effective_start - 1
    return {
        "source": "local_source",
        "available": True,
        "path": _relative(root, target),
        "start_line": effective_start,
        "end_line": actual_end,
        "content": "\n".join(selected),
        "truncated": truncated_by is not None,
        "truncated_by": truncated_by,
    }


__all__ = [
    "LocalSourceError",
    "READ_CHUNK_CHARS",
    "list_tree",
    "read_file",
    "search",
]
