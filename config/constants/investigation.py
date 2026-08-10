"""Constants shared between orchestration routing and investigation stages."""

from __future__ import annotations

from typing import Final

MAX_INVESTIGATION_LOOPS = 20

# Per-investigation duplicate tool-call cache. Lookup stays O(1); eviction is
# LRU by entry count and approximate serialized payload size so long runs
# cannot retain unbounded full results.
INVESTIGATION_TOOL_CACHE_MAX_ENTRIES: Final[int] = 128
INVESTIGATION_TOOL_CACHE_MAX_CHARS: Final[int] = 2_000_000

# Approval tokens auto-expire after this many seconds (5 minutes).
DEFAULT_APPROVAL_EXPIRY_SECONDS: Final[int] = 300

# Sample alert template names accepted by `/investigate <template>` (CLI,
# interactive shell, and messaging gateways). Lives here so gateway code can
# resolve template names without importing the surfaces layer.
ALERT_TEMPLATE_CHOICES: Final[tuple[str, ...]] = (
    "generic",
    "datadog",
    "grafana",
    "honeycomb",
    "coralogix",
    "splunk",
)

# Tool names that dispatch investigation workflows on action surfaces.
# Gated together because they share one capability and are filtered together.
INVESTIGATION_DISPATCH_TOOL_NAMES: Final[tuple[str, ...]] = ("investigation_start", "alert_sample")

# Tool names that can launch detached investigations and should have their
# timeline rows left open when a detached run is accepted.
DETACHED_LAUNCH_TOOL_NAMES: Final[tuple[str, ...]] = (
    "investigation_start",
    "alert_sample",
    "slash_invoke",
)

# The timeline row a detached launch leaves open cannot be closed later — the
# turn's stream has already ended by the time the background run finishes —
# so it is retitled once, here, rather than left showing the original tool
# call. Chat surfaces have no status value for "still running elsewhere";
# this is the honest label for the state the row is stuck in.
DETACHED_LAUNCH_ROW_TITLE: Final[str] = (
    "Investigation handed off — updates will follow in this thread"
)

__all__ = [
    "ALERT_TEMPLATE_CHOICES",
    "DEFAULT_APPROVAL_EXPIRY_SECONDS",
    "DETACHED_LAUNCH_ROW_TITLE",
    "DETACHED_LAUNCH_TOOL_NAMES",
    "INVESTIGATION_DISPATCH_TOOL_NAMES",
    "INVESTIGATION_TOOL_CACHE_MAX_CHARS",
    "INVESTIGATION_TOOL_CACHE_MAX_ENTRIES",
    "MAX_INVESTIGATION_LOOPS",
]
