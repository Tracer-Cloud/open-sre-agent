"""Safe read-only runtime metadata for sessions and sandboxed agent tools.

Populated at session init so agents can answer introspection questions
(e.g. OpenSRE version) without shelling out. Subprocess remains blocked in
the Python execution sandbox; this is the preferred alternative.

Public facade only. Split by concern:

- :mod:`.contract` — the fact key sets and the shell-command deny-list.
- :mod:`.probes` — pure-Python environment probes (host, process, tools, cloud).
- :mod:`.build_info` — git build-marker reading.
- :mod:`.assembly` — composes the above into the fact dicts.

Facts split by lifetime: session-static facts come from
:func:`build_runtime_metadata` (cached on ``SessionCore.runtime_metadata``);
live facts (time, uptime, disk, memory) come from :func:`capture_runtime_facts`
at each prompt render or sandbox call.
"""

from __future__ import annotations

from config.runtime_metadata.assembly import (
    build_runtime_metadata,
    capture_runtime_facts,
    merge_runtime_into_inputs,
)
from config.runtime_metadata.contract import (
    BLOCKED_INTROSPECTION_COMMANDS,
    LIVE_FACT_KEYS,
    RUNTIME_INPUTS_KEY,
    STATIC_FACT_KEYS,
)

__all__ = [
    "BLOCKED_INTROSPECTION_COMMANDS",
    "LIVE_FACT_KEYS",
    "RUNTIME_INPUTS_KEY",
    "STATIC_FACT_KEYS",
    "build_runtime_metadata",
    "capture_runtime_facts",
    "merge_runtime_into_inputs",
]
