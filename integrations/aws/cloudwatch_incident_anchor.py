"""CloudWatch incident-anchor parser.

Registered with :func:`core.domain.types.incident_anchors.register_anchor_parser`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.domain.types.incident_anchors import SOURCE_ACTIVATED_AT, parse_iso8601

_CLOUDWATCH_MAX_DEPTH = 4
"""Maximum nesting depth probed by ``cloudwatch_incident_anchor``.

Real-world CloudWatch payloads are at most 2 levels deep (SNS ``Message``
or EventBridge ``alarmData`` -> alarm dict). The cap is set generously to
4 so legitimate payloads always parse, while pathologically nested input
(``{"Message": {"Message": {"Message": ...}}}``) cannot recurse into
stack overflow.
"""


def cloudwatch_incident_anchor(
    payload: dict[str, Any], _depth: int = 0
) -> tuple[datetime, str] | None:
    """CloudWatch alarm payloads carry ``StateUpdatedTimestamp``.

    The payload arrives wrapped in SNS, so the actual alarm dict often
    lives inside ``Message`` (which is itself a JSON string). We probe
    both top-level and nested shapes, including EventBridge ``alarmData``.

    Recursion is hard-capped at ``_CLOUDWATCH_MAX_DEPTH`` levels so a
    pathologically deep payload cannot blow the Python stack.
    """
    if _depth >= _CLOUDWATCH_MAX_DEPTH:
        return None
    # Top-level direct match.
    for key in ("StateUpdatedTimestamp", "stateUpdatedTimestamp"):
        value = payload.get(key)
        if isinstance(value, str):
            anchor = parse_iso8601(value)
            if anchor is not None:
                return anchor, SOURCE_ACTIVATED_AT
    # Nested probes.
    for nested_key in ("Message", "alarmData", "alarm"):
        nested = payload.get(nested_key)
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except json.JSONDecodeError:
                continue
        if isinstance(nested, dict):
            result = cloudwatch_incident_anchor(nested, _depth=_depth + 1)
            if result is not None:
                return result
    return None


__all__ = ["cloudwatch_incident_anchor"]
