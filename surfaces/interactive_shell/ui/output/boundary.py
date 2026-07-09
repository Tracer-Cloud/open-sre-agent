"""CLI boundary wiring — observability and integration ports.

Lives in a leaf module so ``environment`` (imported by ``renderers`` and
``tracker`` for utility plumbing) does not import those modules back —
that would create a static import cycle. Entry points (``__main__``,
MCP, remote server) and tests call :func:`install_product_adapters`
from here.
"""

from __future__ import annotations


def install_harness_ports() -> None:
    """Register integrations/tools adapters into :mod:`platform.harness_ports`.

    Harness composition root for the interactive shell and tests. Lives in
    ``surfaces`` (not ``tools``) because ``tools`` and ``integrations`` are
    sibling layers and must not import each other — see ``.importlinter.strict``.
    """
    from integrations.harness_adapters import register_harness_adapters as register_integrations
    from tools.harness_adapters import register_harness_adapters as register_tools

    register_integrations()
    register_tools()


def install_product_adapters() -> None:
    """Wire product adapters into observability and integration ports.

    Call once from each process entry point (CLI, MCP, remote server).
    Idempotent — re-registers the same callables so calling it twice
    is a no-op.

    Wires:
    - debug_print: stderr default → Rich-aware CLI version
    - render_investigation_header: no-op default → Rich panel
    - progress tracker: Noop default → Rich-backed CLI singleton (lazy)
    - remote integrations fetcher: empty default → Tracer Cloud adapter
    - harness ports: catalog/store, tool registry, investigation tools, GitHub scope
    """
    from integrations.tracer.integrations_adapter import (
        fetch_tracer_remote_integrations,
    )
    from platform.harness_ports import set_remote_integrations_fetcher
    from platform.observability.debug import set_debug_printer
    from platform.observability.display import (
        set_investigation_footer_renderer,
        set_investigation_header_renderer,
    )
    from platform.observability.progress import set_progress_tracker_factory
    from surfaces.interactive_shell.ui.output.environment import debug_print
    from surfaces.interactive_shell.ui.output.renderers import (
        render_completed_investigation_footer,
        render_investigation_header,
    )
    from surfaces.interactive_shell.ui.output.tracker import get_tracker

    set_debug_printer(debug_print)
    set_investigation_header_renderer(render_investigation_header)
    set_investigation_footer_renderer(render_completed_investigation_footer)
    set_progress_tracker_factory(get_tracker)
    set_remote_integrations_fetcher(fetch_tracer_remote_integrations)
    install_harness_ports()
