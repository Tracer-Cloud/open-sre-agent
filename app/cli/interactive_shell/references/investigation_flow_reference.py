"""Static grounding for the OpenSRE investigation flow.

The interactive-shell assistant does not run investigations itself, but users
ask how alerts are processed. Keep this aligned with ``app/pipeline/pipeline.py``
and the agent packages under ``app/agent/``.
"""

from __future__ import annotations

_INVESTIGATION_FLOW_REFERENCE = """\
Source files:
- app/pipeline/pipeline.py coordinates resolve → extract → investigate → deliver.
- app/pipeline/runners.py exposes run_investigation / run_chat for CLI and tests.
- app/agent/context.py resolves integrations from local configuration.
- app/agent/extract.py parses the raw alert into structured state.
- app/agent/investigation.py runs the connected investigation agent (tools + LLM).
- app/delivery/ publishes findings (terminal, Slack, GitLab writeback, etc.).
- app/state/agent_state.py defines AgentState / InvestigationState.

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
