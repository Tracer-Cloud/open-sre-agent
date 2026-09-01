"""Classifying what an investigation missed, for a partial/inaccurate rating."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from surfaces.shared.terminal.feedback.analytics import _emit_miss_classified
from surfaces.shared.terminal.feedback.prompts import _DIM, _RESET, _pick_taxonomy

if TYPE_CHECKING:
    from rich.console import Console


def _classify_miss(
    record: dict[str, Any],
    *,
    final_state: dict[str, Any],
    console: Console | None,
) -> dict[str, Any] | None:
    """Prompt for taxonomy classification and persist a miss record.

    Returns the miss record on success, ``None`` if the user cancels the
    taxonomy picker (the rating + note are still kept in feedback.jsonl).
    """
    from core.domain.feedback import MissTaxonomy, record_miss
    from infrastructure.terminal.theme import BRAND, DIM, PROMPT_ACCENT_ANSI

    if console is not None:
        console.print(
            f"\n[{BRAND}]Where did this miss come from?[/] [{DIM}]↑↓ · Enter · Esc to skip[/]"
        )
    else:
        sys.stdout.write(
            f"\n{PROMPT_ACCENT_ANSI}Where did this miss come from?{_RESET}"
            f"  {_DIM}↑↓ · Enter · Esc to skip{_RESET}\n\n"
        )
        sys.stdout.flush()

    taxonomy_key = _pick_taxonomy(console=console)
    if not taxonomy_key:
        return None

    try:
        taxonomy = MissTaxonomy(taxonomy_key)
    except ValueError:
        taxonomy = MissTaxonomy.UNKNOWN

    persisted = record_miss(
        record,
        taxonomy=taxonomy,
        taxonomy_detail=record.get("note", ""),
        final_state=final_state,
    )
    if persisted is None:
        # record_miss already surfaced the OSError to stderr; suppress the
        # "saved" confirmation and analytics so the user is not misled.
        return None
    miss_record: dict[str, Any] = dict(persisted)
    _emit_miss_classified(miss_record)
    return miss_record
