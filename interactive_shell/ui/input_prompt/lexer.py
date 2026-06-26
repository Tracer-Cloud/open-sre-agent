"""Lexer for styling REPL input while the user types."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer

from interactive_shell.harness.orchestration.command_dispatch.catalog import (
    BARE_COMMAND_ALIASES,
)


class ReplInputLexer(Lexer):
    """Style the command token (slash form or bare alias) like Claude Code."""

    _CMD_STYLE = "class:repl-slash-command"

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        lines = document.lines

        def get_line(lineno: int) -> StyleAndTextTuples:
            try:
                line = lines[lineno]
            except IndexError:
                return []
            if not line:
                return [("", line)]
            leading = len(line) - len(line.lstrip(" \t"))
            lead, stripped = line[:leading], line[leading:]
            if not stripped:
                return [("", line)]

            if stripped.startswith("/"):
                i = 0
                while i < len(stripped) and not stripped[i].isspace():
                    i += 1
                cmd, rest = stripped[:i], stripped[i:]
                out: StyleAndTextTuples = []
                if lead:
                    out.append(("", lead))
                out.append((self._CMD_STYLE, cmd))
                if rest:
                    out.append(("", rest))
                return out

            parts = stripped.split(maxsplit=1)
            first = parts[0]
            tail = stripped[len(first) :]
            if first.lower() in BARE_COMMAND_ALIASES:
                bare_line: StyleAndTextTuples = []
                if lead:
                    bare_line.append(("", lead))
                bare_line.append((self._CMD_STYLE, first))
                if tail:
                    bare_line.append(("", tail))
                return bare_line

            return [("", line)]

        return get_line
