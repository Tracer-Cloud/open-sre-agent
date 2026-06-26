"""Prompt rendering and prompt-toolkit wiring for the interactive shell."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
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

from cli.interactive_shell.command_registry import SLASH_COMMANDS
from cli.interactive_shell.command_registry.help import QUICK_ACCESS_COMMANDS
from cli.interactive_shell.command_registry.types import SlashCommand
from cli.interactive_shell.history import load_prompt_history
from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch.catalog import (
    BARE_COMMAND_ALIASES,
)
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.ui import (
    ANSI_DIM,
    ANSI_RESET,
    BG,
    DIM,
    DIM_COUNTER_ANSI,
    HIGHLIGHT,
    PROMPT_ACCENT_ANSI,
    PROMPT_FRAME_ANSI,
    TEXT,
    repl_tty_interactive,
)

_PROMPT_RULE_CHAR = "─"
# Keystroke escape (xterm modifyOtherKeys for Shift+Enter), not a colour code.
_SHIFT_ENTER_SEQUENCE = "\x1b[27;2;13~"


def _prompt_rule_line(width: int) -> str:
    return _PROMPT_RULE_CHAR * max(width, 1)


def _prompt_rule_ansi() -> str:
    return f"{PROMPT_FRAME_ANSI}{_prompt_rule_line(_terminal_columns())}{ANSI_RESET}"


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


_DEFAULT_TERMINAL_COLUMNS = 80
_COMPLETION_META_PADDING = 6
_COMPLETION_META_MIN_WIDTH = 24
_COMPLETION_PREVIEW_SEP = " — "


def _terminal_columns() -> int:
    app = get_app_or_none()
    if app is None:
        return _DEFAULT_TERMINAL_COLUMNS
    try:
        return app.output.get_size().columns
    except Exception:
        return _DEFAULT_TERMINAL_COLUMNS


def _clip_text(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _completion_meta_width(command_name: str, cols: int) -> int:
    return max(_COMPLETION_META_MIN_WIDTH, cols - len(command_name) - _COMPLETION_META_PADDING)


def _short_meta(
    text: str,
    *,
    command_name: str = "",
    max_len: int | None = None,
    cols: int | None = None,
) -> str:
    if max_len is None:
        if command_name:
            max_len = _completion_meta_width(command_name, cols or _terminal_columns())
        else:
            max_len = 54
    return _clip_text(text, max_len)


def _slash_command_name(completion: Completion) -> str | None:
    for candidate in (completion.text, completion.display_text or ""):
        if candidate.startswith("/"):
            return candidate
    return None


def _resolve_completion_preview(
    completion: Completion,
    *,
    buffer_text: str,
) -> tuple[str, str] | None:
    cmd_name = _slash_command_name(completion)
    if cmd_name is not None:
        entry = SLASH_COMMANDS.get(cmd_name)
        if entry is not None:
            return cmd_name, entry.description

    meta = completion.display_meta_text
    if not meta:
        return None

    display = completion.display_text or completion.text
    if cmd_name is not None:
        label = display
    else:
        parts = buffer_text.split()
        label = f"{parts[0]} {display}" if parts and parts[0].startswith("/") else display
    return label, meta


def completion_preview_hint_ansi() -> str:
    """Full description for the highlighted completion menu item."""
    app = get_app_or_none()
    if app is None:
        return ""
    buffer = app.current_buffer
    complete_state = buffer.complete_state
    if complete_state is None or not complete_state.completions:
        return ""

    completion = complete_state.current_completion or complete_state.completions[0]
    preview = _resolve_completion_preview(completion, buffer_text=buffer.text)
    if preview is None:
        return ""

    label, description = preview
    try:
        cols = app.output.get_size().columns
    except Exception:
        cols = _DEFAULT_TERMINAL_COLUMNS
    line = _clip_text(f"{label}{_COMPLETION_PREVIEW_SEP}{description}", cols)
    return f"{ANSI_DIM}{line}{ANSI_RESET}"


def resolve_prompt_prefix_ansi(*, inline_spinner: str, idle_hint: str) -> str:
    """Choose the prompt's top context line: spinner, completion preview, or idle hint."""
    if inline_spinner:
        return inline_spinner
    preview = completion_preview_hint_ansi()
    return preview or idle_hint


# Precomputed at import time so bare-`/` completions never rebuild it per keystroke.
_QUICK_ACCESS_SET: frozenset[str] = frozenset(QUICK_ACCESS_COMMANDS)


def _slash_completion(cmd: SlashCommand, start_position: int, *, cols: int) -> Completion:
    return Completion(
        cmd.name,
        start_position=start_position,
        display=cmd.name,
        display_meta=_short_meta(cmd.description, command_name=cmd.name, cols=cols),
    )


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
            cols = _terminal_columns()
            if needle == "/":
                # Bare `/`: show most important commands first, then the rest.
                for name in QUICK_ACCESS_COMMANDS:
                    cmd = SLASH_COMMANDS.get(name)
                    if cmd is not None:
                        yield _slash_completion(cmd, -1, cols=cols)
                for cmd in SLASH_COMMANDS.values():
                    if cmd.name not in _QUICK_ACCESS_SET:
                        yield _slash_completion(cmd, -1, cols=cols)
            else:
                for cmd in SLASH_COMMANDS.values():
                    if cmd.name.lower().startswith(needle):
                        yield _slash_completion(cmd, -len(parts[0]), cols=cols)
            return

        if len(parts) <= 2:
            cmd_name = parts[0].lower()
            raw_arg = "" if trailing_space or len(parts) < 2 else parts[1]

            if _suppress_empty_arg_completions_for_inline_picker(cmd_name, raw_arg):
                return

            if cmd_name in ("/investigate", "/save"):
                if cmd_name == "/investigate":
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
            # prompt_toolkit defaults the ``bottom-toolbar`` style to
            # ``reverse:noinherit``, which paints the toolbar as a dark
            # highlighted band across the terminal. Clear the reverse
            # so the spinner + hint sit on the regular terminal bg
            # (Claude Code-style flat layout).
            "bottom-toolbar": "noreverse",
            "bottom-toolbar.text": "noreverse",
        }
    )


_DEFAULT_PLACEHOLDER_TEXT = "Type a message, /command, or paste an alert"
_DEFAULT_PLACEHOLDER_ANSI = ANSI(f"{ANSI_DIM}{_DEFAULT_PLACEHOLDER_TEXT}{ANSI_RESET}")


def resolve_prompt_placeholder(session: ReplSession) -> ANSI:
    """Contextual ghost text when the input buffer is empty."""
    parts: list[str] = []
    if session.trust_mode:
        parts.append("trust on")
    running = session.task_registry.running_count()
    if running:
        parts.append(f"{running} task{'s' if running != 1 else ''} running")
    if session.resumed_from_name:
        parts.append(f"resumed: {_short_meta(session.resumed_from_name, max_len=32)}")
    if parts:
        return ANSI(f"{ANSI_DIM}{' · '.join(parts)}{ANSI_RESET}")
    return _DEFAULT_PLACEHOLDER_ANSI


def wire_prompt_refresh(
    session: ReplSession,
    pt_app: Any,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[], None]:
    """Register session hook to prefill pending text and redraw the active prompt."""

    def invalidate_prompt() -> None:
        loop.call_soon_threadsafe(pt_app.invalidate)

    def refresh_active_prompt() -> None:
        def _apply() -> None:
            pending = session.pending_prompt_default
            buffer = pt_app.current_buffer
            # Never clobber text the user is actively typing.
            if not pending or buffer.text:
                invalidate_prompt()
                return
            if session.pending_prompt_autosubmit:
                # Auto-submit an agent-queued interactive command so it dispatches
                # through the normal exclusive-stdin path (the only place an
                # interactive child process gets clean stdin). Note: pt_app.is_running
                # under-reports while prompt_async awaits during a dispatch, so we do
                # not gate on it; validate_and_handle works regardless. If the app is
                # genuinely not accepting input, leave the prefill in place so the
                # next prompt iteration picks it up via the before-prompt path.
                session.pending_prompt_default = None
                session.take_pending_autosubmit()
                buffer.text = pending
                try:
                    buffer.validate_and_handle()
                except Exception:  # noqa: BLE001
                    session.pending_prompt_default = pending
                    session.pending_prompt_autosubmit = True
            elif pt_app.is_running:
                session.pending_prompt_default = None
                buffer.text = pending
            invalidate_prompt()

        loop.call_soon_threadsafe(_apply)

    session.prompt_refresh_fn = refresh_active_prompt
    return invalidate_prompt


# Commands where bare invocation opens an inline picker in TTY mode.
_INLINE_PICKER_COMMANDS: frozenset[str] = frozenset(
    {
        "/history",
        "/integrations",
        "/investigate",
        "/mcp",
        "/model",
        "/template",
        "/tests",
        "/trust",
        "/verbose",
    }
)


def _suppress_empty_arg_completions_for_inline_picker(cmd_name: str, raw_arg: str) -> bool:
    """Hide first-arg autocomplete when bare slash command opens inline picker."""
    return repl_tty_interactive() and not raw_arg and cmd_name in _INLINE_PICKER_COMMANDS


def _build_prompt_session(session: ReplSession | None = None) -> PromptSession[str]:
    placeholder = (
        (lambda: resolve_prompt_placeholder(session))
        if session is not None
        else _DEFAULT_PLACEHOLDER_ANSI
    )
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
            placeholder=placeholder,
        )
    )


__all__ = [
    "_PROMPT_RULE_CHAR",
    "_SHIFT_ENTER_SEQUENCE",
    "_build_prompt_key_bindings",
    "_build_prompt_session",
    "_build_prompt_style",
    "_prompt_message",
    "_prompt_rule_ansi",
    "_tab_expand_or_menu",
    "_install_prompt_frame",
    "ReplInputLexer",
    "ShellCompleter",
    "completion_preview_hint_ansi",
    "render_submitted_prompt",
    "resolve_prompt_placeholder",
    "resolve_prompt_prefix_ansi",
    "wire_prompt_refresh",
]
