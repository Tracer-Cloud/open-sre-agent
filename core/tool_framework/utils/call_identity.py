"""Canonical identity for a tool call: its arguments, and which call is live.

Two surfaces ask the same question — "has this exact call already run this
turn?" — and both need an answer that survives the ways a model respells the
same request: keys in a different order, a value that only ``repr`` can
render. The investigation loop reuses the cached result; the action loop
blocks the call outright. Only the canonicalisation is shared, because the
action loop compares *public* arguments (injected credentials merge in later
and would make every call look unique) while the investigation loop compares
what the provider sent.

``bound_tool_call_id``/``current_tool_call_id`` answer a different question —
"which call, by id, is executing right now on this thread" — for code nested
arbitrarily deep under ``core.execution``'s single choke point
(``execute_tool_calls``) that needs to attribute an effect (e.g. a detached
launch) to the one call that caused it, without every intermediate layer's
signature threading the id through explicitly.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


def canonical_argument_signature(arguments: Any) -> str:
    """Return an order-independent, comparable identity for ``arguments``.

    ``default=str`` keeps a value the encoder cannot reach from raising: an
    identity that throws would turn a duplicate-call guard into an outage.
    Objects without a stable ``repr`` may then compare unequal across calls,
    which fails open — the call runs again, as it did before the guard.
    """
    try:
        return json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(arguments)


_CURRENT_TOOL_CALL_ID: ContextVar[str | None] = ContextVar("_CURRENT_TOOL_CALL_ID", default=None)


@contextmanager
def bound_tool_call_id(call_id: str | None) -> Iterator[None]:
    """Bind the id of the tool call executing on this thread right now.

    Set once, at ``core.execution``'s per-call choke point — never per surface —
    so any code that later runs synchronously underneath one tool invocation
    (however many layers down) can read back exactly which call it is inside.
    A `ThreadPoolExecutor` worker does not inherit this from the submitting
    thread, but that is fine here: the set/reset pair below always runs *on*
    the worker thread that executes the call, around that call alone.
    """
    token = _CURRENT_TOOL_CALL_ID.set(call_id)
    try:
        yield
    finally:
        _CURRENT_TOOL_CALL_ID.reset(token)


def current_tool_call_id() -> str | None:
    """Return the id of the tool call currently executing, or ``None`` outside one."""
    return _CURRENT_TOOL_CALL_ID.get()
