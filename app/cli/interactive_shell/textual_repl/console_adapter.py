"""Console adapter that lets existing handlers write into the textual log.

Handlers (``answer_cli_help``, ``answer_cli_agent``, ``answer_follow_up``,
slash command implementations) take a :class:`rich.console.Console` and
call ``console.print(...)``. To avoid rewriting every handler, this
adapter exposes the subset of the Console API they actually use and
forwards calls into the textual app's :class:`textual.widgets.RichLog`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from app.cli.interactive_shell.textual_repl.app import OpenSREApp


class TextualConsole(Console):
    """Rich Console that writes into the textual app's log instead of stdout.

    Inherits from :class:`rich.console.Console` so handler signatures
    (``console: Console``) accept it without changes. Overrides ``print``
    to capture renderables and forward them to ``app.log_widget``; other
    methods (``use_theme``, ``print_exception``) inherit from the base —
    ``use_theme`` ends up a no-op for our purposes since RichLog manages
    its own styling, but the inherited context-manager shape is compatible.
    """

    def __init__(self, app: OpenSREApp) -> None:
        super().__init__(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
        )
        self._app = app

    @property
    def is_terminal(self) -> bool:
        return True

    def print(self, *objects: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """Forward each renderable to the textual log.

        ``RichLog.write`` accepts Rich renderables directly (``Markdown``,
        ``Table``, ``Text``, plain strings), so we don't need to render to
        ANSI here — textual handles the rendering inside its layout.

        Dispatch handlers run on a worker thread (see
        ``OpenSREApp._run_dispatch``); textual's render isn't thread-safe,
        so we marshal each write back to the main thread via
        ``call_from_thread``. That's a no-op cost when we're already on
        the main thread (the textual driver detects it).
        """
        if not objects:
            return
        for obj in objects:
            self._app.call_from_thread(self._app.log_widget.write, obj)

    def update_streaming_progress(self, bytes_received: int) -> None:
        """Streaming hook — used by ``stream_to_console`` to update the
        ``StatusLine`` token counter while a response streams.

        Marshalled to the main thread for the same reason as ``print``.
        """
        self._app.call_from_thread(self._app.update_streaming_progress, bytes_received)


__all__ = ["TextualConsole"]
