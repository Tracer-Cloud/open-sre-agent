"""Shell-owned grounding corpora for interactive-shell agent prompts."""

from __future__ import annotations

from interactive_shell.agent_shell.grounding.agents_md_reference import (
    AgentsMdFile,
    AgentsMdReference,
)
from interactive_shell.agent_shell.grounding.cli_reference import CliReference
from interactive_shell.agent_shell.grounding.context import GroundingContext
from interactive_shell.agent_shell.grounding.docs_reference import DocPage, DocsReference
from interactive_shell.agent_shell.grounding.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)

__all__ = [
    "AgentsMdFile",
    "AgentsMdReference",
    "CliReference",
    "DocPage",
    "DocsReference",
    "GroundingContext",
    "build_investigation_flow_reference_text",
]
