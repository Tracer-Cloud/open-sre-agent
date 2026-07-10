"""Tree-sitter import extraction across supported languages."""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Parser, Query, QueryCursor

from tools.architecture_issue_tool.scanners._paths import iter_source_files, rel_path
from tools.architecture_issue_tool.scanners.import_graph.languages.registry import (
    extension_to_language,
    get_language,
    get_query,
)
from tools.architecture_issue_tool.scanners.import_graph.models import RawImport

_STRING_RE = re.compile(r"""^['"`](.+?)['"`]$""")


def _normalize_import_text(raw: str) -> str:
    text = raw.strip()
    match = _STRING_RE.match(text)
    if match:
        return match.group(1)
    return text


def _extract_from_source(
    *,
    language: str,
    source: bytes,
    source_file: str,
) -> list[RawImport]:
    lang = get_language(language)
    query_text = get_query(language)
    if lang is None or query_text is None:
        return []

    parser = Parser(lang)
    tree = parser.parse(source)
    query = Query(lang, query_text)
    cursor = QueryCursor(query)
    imports: list[RawImport] = []
    seen: set[tuple[str, int]] = set()

    for _pattern_index, captures in cursor.matches(tree.root_node):
        for nodes in captures.values():
            for node in nodes:
                import_spec = _normalize_import_text(node.text.decode("utf-8", errors="replace"))
                if not import_spec:
                    continue
                line = node.start_point[0] + 1
                key = (import_spec, line)
                if key in seen:
                    continue
                seen.add(key)
                imports.append(
                    RawImport(
                        source_file=source_file,
                        import_spec=import_spec,
                        line=line,
                        language=language,
                    )
                )
    return imports


def extract_raw_imports(clone_root: Path) -> list[RawImport]:
    """Extract import/include strings from all supported source files."""
    imports: list[RawImport] = []
    for path in iter_source_files(clone_root):
        language = extension_to_language(path.suffix)
        if language is None:
            continue
        try:
            source = path.read_bytes()
        except OSError:
            continue
        source_file = rel_path(clone_root, path)
        imports.extend(
            _extract_from_source(language=language, source=source, source_file=source_file)
        )
    return imports
