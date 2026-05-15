"""Backward-compat re-exports for the RCA report formatters.

This module used to own both the public entry points (``format_slack_message``,
``build_slack_blocks``, ``format_telegram_message``) and the shared derivation
helpers. Both groups have moved:

- The entry points live in
  ``app.delivery.publish_findings.formatters.renderers.{slack,telegram}``.
- The shared helpers (``_sanitize_for_slack``, ``_to_telegram_html_body``,
  ``_mrkdwn_section``, ``_derive_root_cause_sentence``, ``_get_top_error_log``,
  ``_resolve_evidence_tags``, ``_format_provenance_lines``,
  ``_format_correlation_lines``, ``render_cloudwatch_link``,
  ``render_cloudwatch_link_html``) live in
  ``app.delivery.publish_findings.formatters._derive``.

Both groups are re-exported here so existing imports (notably
``app.delivery.publish_findings.node``) and any external callers continue
to work. The split breaks the module-level import cycle CodeQL flagged on
PR #2057: ``report.py`` is now a leaf re-export module that nothing else
in the package depends on transitively.
"""

from app.delivery.publish_findings.formatters._derive import (
    render_cloudwatch_link,
    render_cloudwatch_link_html,
)
from app.delivery.publish_findings.formatters.renderers.slack import (
    build_slack_blocks,
    format_slack_message,
)
from app.delivery.publish_findings.formatters.renderers.telegram import (
    format_telegram_message,
)

__all__ = [
    "build_slack_blocks",
    "format_slack_message",
    "format_telegram_message",
    "render_cloudwatch_link",
    "render_cloudwatch_link_html",
]
