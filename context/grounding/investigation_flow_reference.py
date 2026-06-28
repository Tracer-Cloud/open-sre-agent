"""Static grounding for the OpenSRE investigation flow.

The interactive-shell assistant does not run investigations itself, but users
ask how alerts are processed. Keep this aligned with
``tools/investigation/lifecycle.py`` and the shared state contracts under
``context/state``.
"""

from __future__ import annotations

_INVESTIGATION_FLOW_REFERENCE = """\
Source files:
- tools/investigation/lifecycle.py coordinates resolve → extract → investigate → deliver.
- tools/investigation/capability.py exposes run_investigation for CLI, SDK, and tests.
- tools/investigation/stages/resolve_integrations/node.py resolves integrations.
- tools/investigation/stages/intake/node.py parses the raw alert into structured state.
- tools/investigation/stages/gather_evidence/agent.py runs the connected investigation agent (tools + LLM).
- tools/investigation/stages/diagnose/node.py parses the agent conclusion into structured RCA fields.
- tools/investigation/reporting/ publishes findings (terminal, Slack, GitLab writeback, etc.).
- context/state/models.py defines AgentState / InvestigationState.

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
