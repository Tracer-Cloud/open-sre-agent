"""Interactive-shell grounding sources for the agent prompts.

The action/assistant prompt builders now live in the decoupled :mod:`core.agent.prompts`
package. This package retains only the shell-specific *grounding* corpora under
:mod:`interactive_shell.agent_shell.llm_context.grounding` (CLI help, repo map, docs),
which the shell exposes to the engine through a ``PromptContextProvider`` adapter.
"""

from __future__ import annotations
