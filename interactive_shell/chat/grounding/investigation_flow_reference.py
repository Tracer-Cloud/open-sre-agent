"""Static grounding for the OpenSRE investigation flow.

The interactive-shell assistant does not run investigations itself, but users
ask how alerts are processed. Keep this aligned with
``core/orchestration/pipeline.py`` and the investigation packages under
``core/``.
"""

from __future__ import annotations

_INVESTIGATION_FLOW_REFERENCE = """\
Source files:
- core/orchestration/pipeline.py coordinates resolve → extract → investigate → deliver.
- core/orchestration/entrypoints.py exposes run_investigation for CLI, SDK, and tests.
- core/orchestration/node/resolve_integrations/node.py resolves integrations.
- core/orchestration/node/extract_alert/node.py parses the raw alert into structured state.
- core/orchestration/node/investigate/agent.py runs the connected investigation agent (tools + LLM).
- core/orchestration/node/diagnose/node.py parses the agent conclusion into structured RCA fields.
- core/orchestration/node/publish_findings/ publishes findings (terminal, Slack, GitLab writeback, etc.).
- core/domain/state/agent_state.py defines AgentState / InvestigationState.

Entry:
- ``opensre investigate`` and pasted alerts in the interactive shell invoke
  ``run_investigation`` (or the streaming/async variants), which follows the
  pipeline above.

Important distinction:
- The interactive terminal assistant answers CLI and architecture questions;
  it does not execute the investigation pipeline itself.
- Do not say the pipeline definition is unavailable; summarize this reference
  and point to the files above.
"""


def build_investigation_flow_reference_text() -> str:
    """Return a concise architectural reference for the interactive assistant."""
    return _INVESTIGATION_FLOW_REFERENCE


__all__ = ["build_investigation_flow_reference_text"]
