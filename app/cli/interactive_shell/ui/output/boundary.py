"""CLI → observability port wiring (boundary module).

Lives in a leaf module so ``environment`` (imported by ``renderers`` and
``tracker`` for utility plumbing) does not import those modules back —
that would create a static import cycle. ``__main__`` and tests call
:func:`install_cli_observability_adapters` from here.
"""

from __future__ import annotations


def install_cli_observability_adapters() -> None:
    """Wire CLI implementations into the observability ports.

    Call once from the CLI boundary (typically the REPL/CLI start-up).
    Idempotent — re-registers the same callables so calling it twice
    is a no-op.

    Wires:
    - debug_print: stderr default → Rich-aware CLI version
    - render_investigation_header: no-op default → Rich panel
    - progress tracker: Noop default → Rich-backed CLI singleton (lazy)
    """
    from app.cli.interactive_shell.ui.output.environment import debug_print
    from app.cli.interactive_shell.ui.output.renderers import (
        render_completed_investigation_footer,
        render_investigation_header,
    )
    from app.cli.interactive_shell.ui.output.tracker import get_tracker
    from app.observability.debug import set_debug_printer
    from app.observability.display import (
        set_investigation_footer_renderer,
        set_investigation_header_renderer,
    )
    from app.observability.progress import set_progress_tracker_factory

    set_debug_printer(debug_print)
    set_investigation_header_renderer(render_investigation_header)
    set_investigation_footer_renderer(render_completed_investigation_footer)
    # Lazy: first core ``get_progress_tracker()`` call constructs the CLI
    # tracker after REPL boot so ``_repl_progress_active()`` is accurate.
    set_progress_tracker_factory(get_tracker)
