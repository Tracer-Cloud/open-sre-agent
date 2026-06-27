"""Shell action-agent tools, execution gating, and terminal action execution.

Import submodules explicitly (for example ``orchestration.agent_actions`` or
``orchestration.tools``) rather than re-exporting from this package
initializer: pulling the full facade in here runs during early ``commands`` →
``command_registry`` import wiring and triggers circular-import failures during
interactive shell startup.
"""

from __future__ import annotations
