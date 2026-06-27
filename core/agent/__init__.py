"""Decoupled agentic turn engine.

This package owns the surface-agnostic agentic loop and turn harness, extracted
out of ``interactive_shell`` so the same engine can drive the interactive
terminal **and** be executed headlessly via a plain API call
(:func:`core.agent.api.run_agent_turn`).

Hard boundary: nothing under ``agent/`` may import from ``interactive_shell``.
The dependency direction is one-way: ``interactive_shell -> agent -> core.runtime``.
See ``agent/AGENTS.md``.
"""

from __future__ import annotations
