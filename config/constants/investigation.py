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
    "new_relic",
)

# Menu labels for the sample alert templates, in the order both terminal
# surfaces list them.
SAMPLE_ALERT_OPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("generic", "Generic - High error rate in payments ETL"),
    ("datadog", "Datadog - payments-etl error rate high"),
    ("grafana", "Grafana - Pipeline failure rate high"),
    ("honeycomb", "Honeycomb - checkout-api latency regression"),
    ("coralogix", "Coralogix - payments worker errors"),
    ("new_relic", "New Relic - checkout-latency threshold breach"),
    ("splunk", "Splunk - payments service error spike"),
)

__all__ = [
    "ALERT_TEMPLATE_CHOICES",
    "DEFAULT_APPROVAL_EXPIRY_SECONDS",
    "INVESTIGATION_TOOL_CACHE_MAX_CHARS",
    "INVESTIGATION_TOOL_CACHE_MAX_ENTRIES",
    "MAX_INVESTIGATION_LOOPS",
    "SAMPLE_ALERT_OPTIONS",
]
