"""Evidence source type — a vendor/integration key identifying a data source.

Not a closed enum: each ``integrations/<vendor>/`` package owns its own
source string(s) (the value passed as ``source=`` when registering a tool).
Core only declares the type alias; it must not hardcode the set of vendors.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

EvidenceSource = str

#: Lifts a tool's raw dict output into the canonical report keys the evidence
#: catalog cites. Called as ``mapper(evidence, output, tool_input)`` and mutates
#: ``evidence`` in place. Declared per tool (``@tool(evidence_mapper=...)``) so a
#: vendor's mapping lives with the vendor's tool, never in a shared stage file.
EvidenceMapper = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]
