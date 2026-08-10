"""The stage-label table must describe the stages the pipeline actually reports.

A key that matches no node name is invisible: ``INVESTIGATION_STAGE_LABELS.get``
falls back to ``DEFAULT_STAGE_LABEL`` and the chat reader watching a five-stage
run sees "Processing" five times. It shipped that way once — four of the five
keys were invented — and nothing failed, because the fallback is the same code
path a genuinely unknown stage takes.
"""

from __future__ import annotations

import re
from pathlib import Path

from config.constants.investigation_stages import (
    DEFAULT_STAGE_LABEL,
    INVESTIGATION_STAGE_LABELS,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEARCH_ROOTS = ("tools/investigation", "core")
_START_CALL = re.compile(r"tracker\.start\(\s*\"([a-z_]+)\"")


def _reported_stage_names() -> set[str]:
    """Every literal node name the pipeline passes to ``tracker.start``."""
    found: set[str] = set()
    for root in _SEARCH_ROOTS:
        for path in (_REPO_ROOT / root).rglob("*.py"):
            if "test" in path.parts:
                continue
            found.update(_START_CALL.findall(path.read_text(encoding="utf-8")))
    return found


def test_every_label_names_a_stage_the_pipeline_reports() -> None:
    """A label for a stage that no longer exists is dead copy."""
    reported = _reported_stage_names()
    assert reported, "found no tracker.start call sites; the scan is broken, not the table"

    unmatched = set(INVESTIGATION_STAGE_LABELS) - reported
    assert not unmatched, (
        f"these labels match no pipeline stage and can never be shown: {sorted(unmatched)}"
    )


def test_every_reported_stage_has_a_label() -> None:
    """An unlabelled stage reads as 'Processing', which tells the reader nothing."""
    unlabelled = _reported_stage_names() - set(INVESTIGATION_STAGE_LABELS)
    assert not unlabelled, (
        f"these stages would fall back to {DEFAULT_STAGE_LABEL!r}: {sorted(unlabelled)}"
    )
