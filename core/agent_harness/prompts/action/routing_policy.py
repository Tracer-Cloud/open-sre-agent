"""Stable action-planner routing rules owned by the OpenSRE harness."""

from __future__ import annotations

ACTION_ROUTING_POLICY = """\
Action routing (apply before choosing a tool):
* An explicit imperative to investigate, analyze, diagnose, RCA, or root-cause
  a named problem or pasted payload -> investigation_start. This is the only
  path that starts a new investigation immediately.
* A cause question without that explicit instruction ("why is it failing?",
  "what caused this?", "figure out why ...") -> assistant_handoff so the
  conversational evidence pass can answer and offer a full investigation.
* A bare alert, incident description, or symptom paste with no explicit
  investigate/analyze/diagnose/RCA/root-cause instruction -> assistant_handoff
  with evidence_kind="incident", never investigation_start. Service, region,
  deployment, log, and error details do not turn a symptom report into an
  instruction to start an investigation.
  Example: a multi-line "Checkout API is returning HTTP 500s" paste followed
  by Service, Region, Recent deploy, and Logs fields is still exactly one
  assistant_handoff(evidence_kind="incident"), never investigation_start.
* Independent clauses are separate tool calls in one response, in user order.
  "check the health of my OpenSRE and then show all connected services" ->
  slash_invoke(command="/health", args=[]), then
  slash_invoke(command="/integrations", args=["list"]).
  Once both calls succeed, stop: do not retry `/integrations`, and never call
  it without the `list` argument for this request.
"""

__all__ = ["ACTION_ROUTING_POLICY"]
