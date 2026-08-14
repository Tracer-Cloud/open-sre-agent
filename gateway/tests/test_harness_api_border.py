"""Gateway reaches the agent harness only through its curated public surface.

``core.agent_harness.__init__`` is the harness's host API — a curated, lazily
resolved export table that AGENTS.md calls "the package's public surface".
Gateway code had grown imports of eleven internal submodules
(``turns.host_cancel``, ``session_goal.run_until``, ``accounting…``), so any
harness-internal rename broke the gateway. One import path makes the coupling
surface explicit and reviewable: widening it means editing the harness's own
export table, not quietly reaching deeper.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_SUBMODULE_IMPORT = re.compile(r"^\s*(?:from|import)\s+core\.agent_harness\.[a-z_.]+", re.MULTILINE)


def _gateway_sources() -> list[Path]:
    return [
        path
        for path in sorted((REPO_ROOT / "gateway").rglob("*.py"))
        if "__pycache__" not in path.parts and "tests" not in path.parts
    ]


def test_gateway_imports_the_harness_only_through_its_public_surface() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _gateway_sources():
        hits = _SUBMODULE_IMPORT.findall(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = [h.strip() for h in hits]

    assert offenders == {}, (
        "gateway imports agent-harness internals; import the name from "
        "core.agent_harness instead (add it to the curated export table in "
        f"core/agent_harness/__init__.py if it is genuinely host-facing): {offenders}"
    )
