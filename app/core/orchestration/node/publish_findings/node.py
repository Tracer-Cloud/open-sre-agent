"""Publish-findings node — entry points for the investigation pipeline.

Node contract:
    Entrypoint : deliver(state: InvestigationState) -> dict[str, Any]
    Reads      : root_cause, validated_claims, non_validated_claims,
                 remediation_steps, correlation, evidence, resolved_integrations,
                 slack_context, telegram_context, whatsapp_context,
                 discord_context, problem_md, masking_context, opensre_evaluate
    Writes     : slack_message, report, opensre_llm_eval (optional)
"""

from __future__ import annotations

from typing import Any, cast

from app.core.orchestration.node.publish_findings.context import build_report_context
from app.core.orchestration.node.publish_findings.delivery import dispatch_report
from app.core.orchestration.node.publish_findings.evaluation import run_optional_opensre_evaluation
from app.core.orchestration.node.publish_findings.formatters.messages import (
    ReportMessages,
    build_report_messages,
)
from app.core.orchestration.node.publish_findings.renderers.editor import open_in_editor
from app.core.orchestration.node.publish_findings.renderers.terminal import render_report
from app.core.orchestration.node.publish_findings.upstream_correlation import (
    enrich_upstream_correlation,
)
from app.masking import MaskingContext
from app.state import InvestigationState
from app.utils.ingest_delivery import create_investigation_and_attach_url


def deliver(state: InvestigationState) -> dict[str, Any]:
    """Format and deliver the investigation report to all configured channels.

    Returns state updates with slack_message and report fields.
    """
    state_dict = dict(state)
    extra_updates = run_optional_opensre_evaluation(state_dict)
    return {**generate_report(state), **extra_updates}


def generate_report(
    state: InvestigationState,
    *,
    render_terminal: bool = True,
    open_editor: bool = True,
) -> dict[str, Any]:
    """Generate and publish the final RCA report."""
    correlation_updates = enrich_upstream_correlation(state)
    enriched_state = cast(InvestigationState, {**dict(state), **correlation_updates})
    ctx = build_report_context(enriched_state)
    short_summary = enriched_state.get("problem_md")
    messages = build_report_messages(ctx)

    # Restore any masked infrastructure identifiers in user-facing output.
    # No-op when masking is disabled or the state has no placeholders.
    masking_ctx = MaskingContext.from_state(dict(enriched_state))
    messages = ReportMessages(
        slack_text=masking_ctx.unmask(messages.slack_text),
        telegram_html=masking_ctx.unmask(messages.telegram_html),
        whatsapp_text=masking_ctx.unmask(messages.whatsapp_text),
        slack_blocks=masking_ctx.unmask_value(messages.slack_blocks),
    )
    if isinstance(short_summary, str):
        short_summary = masking_ctx.unmask(short_summary)

    investigation_id, investigation_url = create_investigation_and_attach_url(
        enriched_state,
        messages.slack_text,
        short_summary,
    )

    if render_terminal:
        render_report(messages.slack_text)
    if open_editor:
        open_in_editor(messages.slack_text)

    dispatch_report(
        enriched_state,
        messages,
        investigation_id=investigation_id,
        investigation_url=investigation_url,
    )

    return {
        **correlation_updates,
        "slack_message": messages.slack_text,
        "report": messages.slack_text,
    }
