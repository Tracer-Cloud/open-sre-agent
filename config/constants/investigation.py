"""Constants shared between orchestration routing and investigation stages."""

from __future__ import annotations

from typing import Final

MAX_INVESTIGATION_LOOPS = 20

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

__all__ = [
    "ALERT_TEMPLATE_CHOICES",
    "DEFAULT_APPROVAL_EXPIRY_SECONDS",
    "MAX_INVESTIGATION_LOOPS",
]
