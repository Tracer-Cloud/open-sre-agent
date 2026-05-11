"""Source-file chunking primitives for future RAG over the OpenSRE codebase.

This module delivers chunking only — no persistence, embeddings, repo walking,
or cache invalidation. Each chunker is a pure function: it reads the passed-in
Path and returns SourceChunks with no side effects.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.cli.interactive_shell.references.docs_reference import _excerpt, _strip_frontmatter

ChunkKind = Literal["py_func", "py_class", "py_module", "md_section"]

_MAX_CONTENT_CHARS = 6000
_MAX_BODY_LINES = 80


@dataclass(frozen=True)
class SourceChunk:
    relpath: str
    kind: ChunkKind
    symbol: str
    start_line: int
    end_line: int
    content: str


def chunk_python_file(path: Path, *, repo_root: Path) -> list[SourceChunk]:
    """Chunk a Python file by top-level def/class, plus an optional module chunk.

    Returns an empty list for files in ``__pycache__``, private modules
    (basename starts with ``_``), files that fail to parse, or files with
    no top-level definitions and no module preamble.
    """
    if "__pycache__" in path.parts or path.name.startswith("_"):
        return []

    rel = _relpath(path, repo_root)
    if rel is None:
        return []

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # ValueError covers source containing null bytes; SyntaxError covers everything else.
        return []

    source_lines = text.splitlines()
    chunks: list[SourceChunk] = []

    module_chunk = _build_module_chunk(tree, source_lines, rel, path.stem)
    if module_chunk is not None:
        chunks.append(module_chunk)

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        chunks.append(_build_def_chunk(node, source_lines, rel))

    return chunks


def chunk_markdown_file(path: Path, *, repo_root: Path) -> list[SourceChunk]:
    """Chunk a Markdown/MDX file by H2 headings, stripping YAML frontmatter.

    Line numbers are file-relative — i.e. they include any frontmatter lines
    that were stripped before scanning, so a consumer can use them to navigate
    back to the original source location.
    """
    rel = _relpath(path, repo_root)
    if rel is None:
        return []

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    body, frontmatter = _strip_frontmatter(text)
    # Number of source lines consumed by frontmatter; markdown line numbers
    # below are reported as file-relative by adding this offset.
    fm_line_offset = text[: len(text) - len(body)].count("\n") if frontmatter is not None else 0

    if not body.strip():
        return []

    body_lines = body.splitlines()
    section_starts = _find_h2_starts(body_lines)

    if not section_starts:
        return [
            SourceChunk(
                relpath=rel,
                kind="md_section",
                symbol=path.stem,
                start_line=1 + fm_line_offset,
                end_line=len(body_lines) + fm_line_offset,
                content=_truncate_content(body),
            )
        ]

    chunks: list[SourceChunk] = []
    for idx, (start_idx, heading) in enumerate(section_starts):
        end_idx = (
            section_starts[idx + 1][0] - 1 if idx + 1 < len(section_starts) else len(body_lines) - 1
        )
        section_text = "\n".join(body_lines[start_idx : end_idx + 1])
        chunks.append(
            SourceChunk(
                relpath=rel,
                kind="md_section",
                symbol=heading,
                start_line=start_idx + 1 + fm_line_offset,
                end_line=end_idx + 1 + fm_line_offset,
                content=_truncate_content(section_text),
            )
        )
    return chunks


def chunk_path(path: Path, *, repo_root: Path) -> list[SourceChunk]:
    """Dispatch to the right chunker by file suffix; unknown suffixes return ``[]``."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return chunk_python_file(path, repo_root=repo_root)
    if suffix in (".md", ".mdx"):
        return chunk_markdown_file(path, repo_root=repo_root)
    return []


def _relpath(path: Path, repo_root: Path) -> str | None:
    """Return the path's POSIX-style relpath inside repo_root, or None if outside."""
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def _truncate_content(content: str) -> str:
    return _excerpt(content, max_chars=_MAX_CONTENT_CHARS)


def _build_module_chunk(
    tree: ast.Module, source_lines: list[str], rel: str, stem: str
) -> SourceChunk | None:
    last_preamble_node: ast.stmt | None = None
    for node in tree.body:
        if _is_module_docstring(node) or isinstance(node, (ast.Import, ast.ImportFrom)):
            last_preamble_node = node
            continue
        break

    if last_preamble_node is None:
        return None

    end_line = last_preamble_node.end_lineno or last_preamble_node.lineno
    content = "\n".join(source_lines[:end_line])
    return SourceChunk(
        relpath=rel,
        kind="py_module",
        symbol=stem,
        start_line=1,
        end_line=end_line,
        content=_truncate_content(content),
    )


def _is_module_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _build_def_chunk(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    source_lines: list[str],
    rel: str,
) -> SourceChunk:
    start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    real_end_line = node.end_lineno or node.lineno
    body_lines = real_end_line - start_line + 1

    if body_lines > _MAX_BODY_LINES:
        cut_end = start_line + _MAX_BODY_LINES - 1
        kept = source_lines[start_line - 1 : cut_end]
        kept.append(f"# … truncated, real end line: {real_end_line} …")
        content = "\n".join(kept)
    else:
        content = "\n".join(source_lines[start_line - 1 : real_end_line])

    kind: ChunkKind = "py_class" if isinstance(node, ast.ClassDef) else "py_func"
    return SourceChunk(
        relpath=rel,
        kind=kind,
        symbol=node.name,
        start_line=start_line,
        end_line=real_end_line,
        content=_truncate_content(content),
    )


def _find_h2_starts(body_lines: list[str]) -> list[tuple[int, str]]:
    """Return ``(index, heading)`` for every H2 outside fenced code blocks.

    Tracks the opening fence character so a backtick fence is only closed by
    backticks (and tilde by tilde) — mismatched fences don't toggle state.
    """
    open_fence: str | None = None
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(body_lines):
        stripped = line.lstrip()
        if open_fence is None:
            if stripped.startswith("```"):
                open_fence = "```"
                continue
            if stripped.startswith("~~~"):
                open_fence = "~~~"
                continue
        else:
            if stripped.startswith(open_fence):
                open_fence = None
            continue
        if line.startswith("## "):
            starts.append((i, line[3:].strip()))
    return starts
