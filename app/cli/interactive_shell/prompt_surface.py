"""Prompt rendering and prompt-toolkit wiring for the interactive shell."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.text import Text

from app.cli.interactive_shell.commands import SLASH_COMMANDS
from app.cli.interactive_shell.history import load_prompt_history
from app.cli.interactive_shell.router import BARE_COMMAND_ALIASES
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import (
    ANSI_DIM,
    ANSI_RESET,
    BG,
    DIM,
    DIM_COUNTER_ANSI,
    HIGHLIGHT,
    PROMPT_ACCENT_ANSI,
    PROMPT_FRAME_ANSI,
    TEXT,
)

_PROMPT_RULE_CHAR = "─"
# Keystroke escape (xterm modifyOtherKeys for Shift+Enter), not a colour code.
_SHIFT_ENTER_SEQUENCE = "\x1b[27;2;13~"


def _prompt_rule_line(width: int) -> str:
    return _PROMPT_RULE_CHAR * max(width, 1)


def _prompt_rule_ansi() -> str:
    try:
        width = get_app().output.get_size().columns
    except Exception:
        width = 80
    return f"{PROMPT_FRAME_ANSI}{_prompt_rule_line(width)}{ANSI_RESET}"


def _prompt_counter_text(session: ReplSession) -> str:
    return f"[{len(session.history)}] " if session.history else ""


def _prompt_prefix_text(session: ReplSession) -> str:
    return f"{_prompt_counter_text(session)}❯ "


def _prompt_line_ansi(session: ReplSession) -> ANSI:
    counter = _prompt_counter_text(session)
    if counter:
        prefix = f"{DIM_COUNTER_ANSI}{counter}{ANSI_RESET}"
    else:
        prefix = ""
    return ANSI(f"{prefix}{PROMPT_ACCENT_ANSI}❯{ANSI_RESET} ")


def _prompt_message(session: ReplSession) -> ANSI:
    """Top border rule + cursor line — the top two rows of the input box."""
    return ANSI(f"{_prompt_rule_ansi()}\n{_prompt_line_ansi(session).value}")


def render_submitted_prompt(console: Console, session: ReplSession, text: str) -> None:
    """Render the submitted user turn above the streamed assistant response."""
    lines = text.splitlines() or [""]
    continuation_prefix = " " * len(_prompt_prefix_text(session))
    rendered = Text()
    counter = _prompt_counter_text(session)
    if counter:
        rendered.append(counter, style=DIM)
    rendered.append("❯ ", style=f"bold {HIGHLIGHT}")
    rendered.append(lines[0])
    for line in lines[1:]:
        rendered.append("\n")
        rendered.append(continuation_prefix, style=DIM)
        rendered.append(line)
    console.print(rendered)


def _install_prompt_frame(session: PromptSession[str]) -> PromptSession[str]:
    return session


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


def _short_meta(text: str, max_len: int = 54) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


class ShellCompleter(Completer):
    """Tab-completion for slash commands, subcommands, file paths, and bare aliases."""

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text:
            return

        if not text.startswith("/"):
            if " " in text:
                return
            needle = text.lower()
            for alias in sorted(BARE_COMMAND_ALIASES):
                if alias.startswith(needle) and alias != needle:
                    yield Completion(
                        alias,
                        start_position=-len(text),
                        display=alias,
                        display_meta="command shortcut",
                    )
            return

        parts = text.split()
        trailing_space = text != text.rstrip(" ")
        if len(parts) == 1 and not trailing_space:
            needle = parts[0].lower()
            for cmd in SLASH_COMMANDS.values():
                if cmd.name.lower().startswith(needle):
                    yield Completion(
                        cmd.name,
                        start_position=-len(parts[0]),
                        display=cmd.name,
                        display_meta=_short_meta(cmd.help_text),
                    )
            return

        if len(parts) <= 2:
            cmd_name = parts[0].lower()
            raw_arg = "" if trailing_space or len(parts) < 2 else parts[1]
            if cmd_name in ("/investigate", "/save"):
                yield from PathCompleter(expanduser=True).get_completions(
                    Document(raw_arg, len(raw_arg)),
                    complete_event,
                )
                return

            entry = SLASH_COMMANDS.get(cmd_name)
            hints = entry.first_arg_completions if entry is not None else ()
            sub_prefix = raw_arg.lower()
            for sub, meta in hints:
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(raw_arg),
                        display=sub,
                        display_meta=meta,
                    )


def _tab_expand_or_menu(buffer: Buffer) -> None:
    """Apply the current completion or open the menu when several choices exist."""
    if buffer.complete_state:
        state = buffer.complete_state
        completion = state.current_completion
        if completion is None and state.completions:
            completion = state.completions[0]
        if completion is not None:
            buffer.apply_completion(completion)
        return
    if buffer.completer is None:
        return
    completions = list(
        buffer.completer.get_completions(
            buffer.document,
            CompleteEvent(completion_requested=True),
        )
    )
    if len(completions) == 1:
        buffer.apply_completion(completions[0])
    else:
        buffer.start_completion(select_first=True)


def _build_prompt_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("c-m")
    def _accept_turn(event: object) -> None:
        if event.data == _SHIFT_ENTER_SEQUENCE:  # type: ignore[attr-defined]
            event.current_buffer.newline(copy_margin=False)  # type: ignore[attr-defined]
            return
        event.current_buffer.validate_and_handle()  # type: ignore[attr-defined]

    @bindings.add("tab")
    def _tab_complete(event: object) -> None:
        _tab_expand_or_menu(event.current_buffer)  # type: ignore[attr-defined]

    @bindings.add("s-tab")
    def _shift_tab_complete(event: object) -> None:
        buff = event.current_buffer  # type: ignore[attr-defined]
        if buff.complete_state:
            buff.complete_previous()
        else:
            buff.start_completion(select_first=False)

    @bindings.add("down", filter=has_completions)
    def _next_completion(event: object) -> None:
        event.current_buffer.complete_next()  # type: ignore[attr-defined]

    @bindings.add("up", filter=has_completions)
    def _previous_completion(event: object) -> None:
        event.current_buffer.complete_previous()  # type: ignore[attr-defined]

    return bindings


def _build_prompt_style() -> Style:
    return Style.from_dict(
        {
            "prompt-frame-line": f"bold {HIGHLIGHT}",
            "repl-slash-command": f"bold {HIGHLIGHT} bg:{BG}",
            "completion-menu": f"bg:{BG}",
            "completion-menu.completion": f"{TEXT} bg:{BG}",
            "completion-menu.completion.current": f"bold {HIGHLIGHT} bg:{BG}",
            "completion-menu.meta.completion": f"{DIM} bg:{BG}",
            "completion-menu.meta.completion.current": f"{HIGHLIGHT} bg:{BG}",
            "completion-menu.border": DIM,
            "scrollbar.background": f"bg:{BG}",
            "scrollbar.button": f"bg:{DIM}",
        }
    )


_PLACEHOLDER_ANSI = ANSI(f"{ANSI_DIM}Type a message, /command, or paste an alert{ANSI_RESET}")


def _build_prompt_session(_session: ReplSession | None = None) -> PromptSession[str]:
    return _install_prompt_frame(
        PromptSession(
            completer=ShellCompleter(),
            complete_while_typing=True,
            multiline=True,
            reserve_space_for_menu=8,
            history=load_prompt_history(),
            lexer=ReplInputLexer(),
            key_bindings=_build_prompt_key_bindings(),
            style=_build_prompt_style(),
            erase_when_done=True,
            placeholder=_PLACEHOLDER_ANSI,
        )
    )


__all__ = [
    "_PROMPT_RULE_CHAR",
    "_SHIFT_ENTER_SEQUENCE",
    "_build_prompt_key_bindings",
    "_build_prompt_session",
    "_build_prompt_style",
    "_prompt_message",
    "_tab_expand_or_menu",
    "_install_prompt_frame",
    "ReplInputLexer",
    "ShellCompleter",
    "render_submitted_prompt",
]
