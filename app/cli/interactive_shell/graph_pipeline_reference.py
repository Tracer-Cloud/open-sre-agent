"""Static grounding for the OpenSRE LangGraph pipeline.

The interactive-shell assistant is intentionally LangGraph-free, but users ask
it architectural questions about the investigation graph that powers pasted
alerts and ``opensre investigate``. Keep this concise reference aligned with
``app/pipeline/graph.py`` and ``app/pipeline/routing.py`` so those answers are
grounded in the actual pipeline shape.
"""

from __future__ import annotations

_GRAPH_PIPELINE_REFERENCE = """\
Source files:
- app/pipeline/graph.py builds and compiles the LangGraph StateGraph.
- app/pipeline/routing.py owns conditional edge functions.
- app/state/agent_state.py defines AgentState / InvestigationState.
- app/nodes/ contains node implementations.

Entry point:
- inject_auth is always first.
- route_by_mode sends mode="investigation" to extract_alert; all other modes use chat.

Chat flow:
- inject_auth -> router.
- route_chat sends route="tracer_data" to chat_agent, otherwise to general.
- chat_agent loops through tool_executor while the last AI message has tool calls.
- general and completed chat_agent turns end the graph.

Investigation flow:
- inject_auth -> extract_alert.
- route_after_extract ends early for noise alerts.
- non-noise alerts continue to resolve_integrations.
- resolve_integrations -> plan_actions.
- distribute_hypotheses fans planned actions out to investigate_hypothesis in parallel.
- if no planned actions exist, the graph goes directly to merge_hypothesis_results.
- investigate_hypothesis -> merge_hypothesis_results -> diagnose.
- diagnose uses route_investigation_loop.
- if more investigation is recommended, diagnose -> adapt_window -> plan_actions.
- otherwise diagnose -> publish.
- diagnose -> opensre_eval -> publish when OpenSRE eval is enabled.
- publish ends the graph.

Important distinction:
- The interactive terminal assistant does not execute the graph itself.
- Pasting an alert into the interactive shell launches this pipeline.
- running opensre investigate launches this pipeline.
- Do not say the graph definition is unavailable.
- Summarize this reference and point to the files above.
"""


def build_graph_pipeline_reference_text() -> str:
    """Return a concise architectural reference for the interactive assistant."""
    return _GRAPH_PIPELINE_REFERENCE


__all__ = ["build_graph_pipeline_reference_text"]
