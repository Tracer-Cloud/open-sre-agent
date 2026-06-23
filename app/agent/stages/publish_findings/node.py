"""Main orchestration node for report generation and publishing."""

from typing import Any

from app.agent.stages.publish_findings.context import build_report_context
from app.agent.stages.publish_findings.delivery import dispatch_report
from app.agent.stages.publish_findings.formatters.messages import (
    ReportMessages,
    build_report_messages,
)
from app.agent.stages.publish_findings.renderers.editor import open_in_editor
from app.agent.stages.publish_findings.renderers.terminal import render_report
from app.masking import MaskingContext
from app.state import InvestigationState
from app.utils.ingest_delivery import create_investigation_and_attach_url


def generate_report(
    state: InvestigationState,
    *,
    render_terminal: bool = True,
    open_editor: bool = True,
) -> dict[str, Any]:
    """Generate and publish the final RCA report."""
    ctx = build_report_context(state)
    short_summary = state.get("problem_md")
    messages = build_report_messages(ctx)

    # Restore any masked infrastructure identifiers in user-facing output.
    # No-op when masking is disabled or the state has no placeholders.
    masking_ctx = MaskingContext.from_state(dict(state))
    messages = ReportMessages(
        slack_text=masking_ctx.unmask(messages.slack_text),
        telegram_html=masking_ctx.unmask(messages.telegram_html),
        whatsapp_text=masking_ctx.unmask(messages.whatsapp_text),
        slack_blocks=masking_ctx.unmask_value(messages.slack_blocks),
    )
    if isinstance(short_summary, str):
        short_summary = masking_ctx.unmask(short_summary)

    investigation_id, investigation_url = create_investigation_and_attach_url(
        state,
        messages.slack_text,
        short_summary,
    )

    if render_terminal:
        render_report(messages.slack_text)
    if open_editor:
        open_in_editor(messages.slack_text)

    dispatch_report(
        state,
        messages,
        investigation_id=investigation_id,
        investigation_url=investigation_url,
    )

    return {"slack_message": messages.slack_text, "report": messages.slack_text}
