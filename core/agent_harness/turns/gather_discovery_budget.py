"""Cap MCP discovery thrash during evidence gather.

MCP bridges typically expose ``list_*_tools`` plus a ``call_*_tool`` exec
surface where the model explores with ``search`` / ``info`` / ``schema`` and
meta ``call read-data-schema`` before any real metric query. Those discovery
calls succeed, so :class:`SourceCircuitBreaker` never trips — live SessionGoal
metric asks burned 10–15 discovery lines and still returned no count
(parity S1).

This hook:

* Matches bridge tools by naming shape (``list_*_tools`` / ``call_*_tool``),
  not a vendor allow-list.
* Dedupes exact discovery fingerprints for the gather turn (command text;
  ``context`` prose is ignored).
* Caps how many discovery-style calls may run; further discovery is blocked
  with a reason that steers toward one real query or stop.
* Still allows non-discovery ``call <query-tool> …`` after the budget is spent.

Fingerprints use hashable tuples of known fields — never ``json.dumps``.
"""

from __future__ import annotations

from typing import Any

from core.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)

DEFAULT_MAX_DISCOVERY_CALLS = 4

_DISCOVERY_PREFIXES = frozenset({"search", "info", "schema"})
# Meta ``call <tool>`` targets that only explore schemas / skills — not metrics.
_DISCOVERY_CALL_TARGETS = frozenset({"read-data-schema", "skill-get"})

DiscoveryFingerprint = tuple[str, str, str]


def is_mcp_list_tools(tool_name: str) -> bool:
    """True for MCP roster tools named ``list_<vendor>_tools``."""
    name = tool_name.strip()
    return name.startswith("list_") and name.endswith("_tools") and len(name) > len("list__tools")


def is_mcp_exec_bridge(tool_name: str) -> bool:
    """True for MCP exec bridges named ``call_<vendor>_tool``."""
    name = tool_name.strip()
    return name.startswith("call_") and name.endswith("_tool") and len(name) > len("call__tool")


def _nested_exec_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    inner = arguments.get("arguments")
    if isinstance(inner, dict):
        return inner
    return arguments


def _exec_command(arguments: dict[str, Any]) -> str:
    nested = _nested_exec_arguments(arguments)
    raw = nested.get("command")
    if raw is None:
        raw = arguments.get("command")
    return str(raw or "").strip()


def _call_target(command: str) -> str:
    parts = command.split(None, 2)
    if len(parts) < 2 or parts[0].lower() != "call":
        return ""
    return parts[1].split("{", 1)[0].strip().lower()


def is_gather_discovery_call(tool_name: str, arguments: dict[str, Any]) -> bool:
    """Whether this tool call is schema/skill discovery rather than a metric query."""
    name = tool_name.strip()
    if is_mcp_list_tools(name):
        return True
    if not is_mcp_exec_bridge(name):
        return False
    command = _exec_command(arguments)
    if not command:
        return False
    verb = command.split(None, 1)[0].lower()
    if verb in _DISCOVERY_PREFIXES:
        return True
    if verb == "call":
        return _call_target(command) in _DISCOVERY_CALL_TARGETS
    return False


def discovery_fingerprint(tool_name: str, arguments: dict[str, Any]) -> DiscoveryFingerprint:
    """Stable identity for discovery dedupe (ignores free-text ``context``)."""
    name = tool_name.strip()
    if is_mcp_list_tools(name):
        filt = str(arguments.get("name_filter") or "")
        schema = "1" if arguments.get("include_schema") else "0"
        return (name, filt, schema)
    command = _exec_command(arguments)
    # Drop trailing JSON payload noise for call-target identity when present.
    verb_and_target = command.split("{", 1)[0].strip().lower()
    bridge = str(arguments.get("name") or arguments.get("tool") or "exec")
    return (name, bridge, verb_and_target)


def with_gather_discovery_budget(
    base: ToolExecutionHooks | None = None,
    *,
    max_discovery_calls: int = DEFAULT_MAX_DISCOVERY_CALLS,
) -> ToolExecutionHooks:
    """Compose discovery budget + exact-fingerprint dedupe onto gather hooks."""
    budget = max(1, int(max_discovery_calls))
    seen: set[DiscoveryFingerprint] = set()
    discovery_count = 0
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        nonlocal discovery_count
        tool_name = request.tool_call.name
        arguments = dict(request.arguments or {})
        if is_gather_discovery_call(tool_name, arguments):
            key = discovery_fingerprint(tool_name, arguments)
            if key in seen:
                return BeforeToolCallResult(
                    blocked=True,
                    reason=(
                        f"Already ran discovery {tool_name} with the same command "
                        "this gather turn. Do not repeat it — run one real metric "
                        "query (or stop gathering)."
                    ),
                    metadata={"suppressed_duplicate_discovery": True},
                )
            if discovery_count >= budget:
                return BeforeToolCallResult(
                    blocked=True,
                    reason=(
                        f"Discovery budget exhausted ({budget} search/info/schema/"
                        "list calls this gather turn). Stop exploring MCP schemas — "
                        "execute one concrete metric query now, or conclude with "
                        "what you have so the assistant can draft a query + a setup CTA."
                    ),
                    metadata={
                        "discovery_budget_exhausted": True,
                        "max_discovery_calls": budget,
                    },
                )
        if base_before is not None:
            return base_before(request)
        return None

    def after(
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> Any:
        nonlocal discovery_count
        tool_name = request.tool_call.name
        arguments = dict(request.arguments or {})
        if is_gather_discovery_call(tool_name, arguments) and not result.is_error:
            key = discovery_fingerprint(tool_name, arguments)
            if key not in seen:
                seen.add(key)
                discovery_count += 1
        if base_after is not None:
            return base_after(request, result)
        return None

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = [
    "DEFAULT_MAX_DISCOVERY_CALLS",
    "discovery_fingerprint",
    "is_gather_discovery_call",
    "is_mcp_exec_bridge",
    "is_mcp_list_tools",
    "with_gather_discovery_budget",
]
