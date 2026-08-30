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

#: Key under which mappers accumulate citeable report entries in the evidence dict.
CATALOG_ENTRIES_KEY = "catalog_entries"


def record_evidence_entry(
    evidence: dict[str, Any],
    *,
    source: str,
    label: str,
    summary: str | None = None,
    url: str | None = None,
    snippet: str | None = None,
) -> None:
    """Record a citeable report entry from inside an evidence mapper.

    The report's evidence catalog turns each entry into a display id (``E1`` …)
    the agent can cite. ``source`` is the claim-facing key; a bespoke catalog
    reader for the same key takes precedence over this one. A later call with
    the same ``source`` (e.g. the same tool invoked again with a different
    query in one investigation) replaces the earlier entry rather than adding a
    second one, so the catalog always cites the most recent call for that
    source. Lets a tool's output become citeable without editing the shared
    catalog builder.
    """
    entries = evidence.setdefault(CATALOG_ENTRIES_KEY, [])
    if not isinstance(entries, list):
        return
    entries[:] = [e for e in entries if not (isinstance(e, dict) and e.get("source") == source)]
    entries.append(
        {"source": source, "label": label, "summary": summary, "url": url, "snippet": snippet}
    )
