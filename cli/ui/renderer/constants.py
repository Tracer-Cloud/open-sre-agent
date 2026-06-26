"""Shared constants for streamed investigation rendering."""

from __future__ import annotations

from cli.interactive_shell.ui.theme import (
    ANSI_BOLD,
    ANSI_DIM,
    ANSI_RESET,
    BOLD_BRAND_ANSI,
    HIGHLIGHT_ANSI,
    TEXT_ANSI,
)
from platform.analytics.source import EntrypointSource

_RESET = ANSI_RESET
_DIM = ANSI_DIM
_BOLD = ANSI_BOLD
_WHITE = TEXT_ANSI
_GREEN = HIGHLIGHT_ANSI
_CYAN = BOLD_BRAND_ANSI

_NODE_START_KINDS = frozenset({"on_chain_start"})
_NODE_END_KINDS = frozenset({"on_chain_end"})
_TOKEN_STREAM_KIND = "on_chat_model_stream"
_DIAGNOSE_NODE = "diagnose_root_cause"
_DIAGNOSE_LIVE_REFRESH = 20
_DIAGNOSE_RENDER_INTERVAL_S = 1.0 / _DIAGNOSE_LIVE_REFRESH
_DIAGNOSE_SPINNER_NAME = "dots12"
_DIAGNOSE_SPINNER_COLOR = "orange1"
_HIDDEN_PROGRESS_NODES = frozenset({"publish_findings"})


def _render_source(*, local: bool) -> str:
    return EntrypointSource.CLI_PASTE.value if local else EntrypointSource.REMOTE_HTTP.value
