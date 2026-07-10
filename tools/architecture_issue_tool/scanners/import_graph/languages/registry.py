"""Polyglot source file extensions and tree-sitter language registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from tree_sitter import Language

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".lua": "lua",
}

_IMPORT_QUERIES: dict[str, str] = {
    "python": """
(import_from_statement module_name: (dotted_name) @import)
(import_statement name: (dotted_name) @import)
(import_statement name: (aliased_import name: (dotted_name) @import))
""",
    "javascript": """
(import_statement source: (string) @import)
(call_expression
  function: (identifier) @fn
  (#eq? @fn "require")
  arguments: (arguments (string) @import))
""",
    "typescript": """
(import_statement source: (string) @import)
(call_expression
  function: (identifier) @fn
  (#eq? @fn "require")
  arguments: (arguments (string) @import))
""",
    "go": """
(import_declaration (import_spec path: (interpreted_string_literal) @import))
(import_declaration (import_spec path: (raw_string_literal) @import))
""",
    "rust": """
(use_declaration argument: (_) @import)
(extern_crate_declaration name: (identifier) @import)
""",
    "java": """
(import_declaration (scoped_identifier) @import)
(import_declaration (identifier) @import)
""",
    "c": """
(preproc_include path: (string_literal) @import)
(preproc_include path: (system_lib_string) @import)
""",
    "cpp": """
(preproc_include path: (string_literal) @import)
(preproc_include path: (system_lib_string) @import)
""",
    "csharp": """
(using_directive name: (_) @import)
""",
    "ruby": """
(call command: (identifier) @cmd
  arguments: (argument_list (string) @import)
  (#eq? @cmd "require"))
""",
    "php": """
(namespace_use_declaration (namespace_use_clause (qualified_name) @import))
""",
    "kotlin": """
(import_header (identifier) @import)
(import_header (dot_qualified_expression) @import)
""",
    "swift": """
(import_declaration (identifier) @import)
""",
    "scala": """
(import_declaration path: (_) @import)
""",
    "bash": """
(command name: (command_name (word) @import) argument: (word) @path)
""",
    "lua": """
(function_call name: (identifier) @fn arguments: (arguments (string) @import) (#eq? @fn "require"))
""",
}


@dataclass(frozen=True)
class LanguageSpec:
    """Tree-sitter language configuration."""

    name: str
    extensions: tuple[str, ...]
    query: str


def extension_to_language(path_suffix: str) -> str | None:
    return _EXTENSION_TO_LANGUAGE.get(path_suffix.lower())


def supported_extensions() -> frozenset[str]:
    return frozenset(_EXTENSION_TO_LANGUAGE)


@lru_cache(maxsize=32)
def _load_language(language_name: str) -> Language | None:
    module_map: dict[str, tuple[str, str]] = {
        "python": ("tree_sitter_python", "language"),
        "javascript": ("tree_sitter_javascript", "language"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "go": ("tree_sitter_go", "language"),
        "rust": ("tree_sitter_rust", "language"),
        "java": ("tree_sitter_java", "language"),
        "c": ("tree_sitter_c", "language"),
        "cpp": ("tree_sitter_cpp", "language"),
        "csharp": ("tree_sitter_c_sharp", "language"),
        "ruby": ("tree_sitter_ruby", "language"),
        "php": ("tree_sitter_php", "language"),
        "kotlin": ("tree_sitter_kotlin", "language"),
        "swift": ("tree_sitter_swift", "language"),
        "scala": ("tree_sitter_scala", "language"),
        "bash": ("tree_sitter_bash", "language"),
        "lua": ("tree_sitter_lua", "language"),
    }
    entry = module_map.get(language_name)
    if entry is None:
        return None
    module_name, attr = entry
    try:
        import importlib

        module = importlib.import_module(module_name)
        loader: Callable[[], Any] = getattr(module, attr)
        return Language(loader())
    except (ImportError, AttributeError, TypeError, ValueError):
        return None


def get_language(language_name: str) -> Language | None:
    return _load_language(language_name)


def get_query(language_name: str) -> str | None:
    return _IMPORT_QUERIES.get(language_name)
