"""Victoria Logs integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from integrations.victoria_logs.client import VictoriaLogsClient, VictoriaLogsConfig

verify_victoria_logs = register_probe_verifier(
    "victoria_logs",
    config=VictoriaLogsConfig.model_validate,
    client=VictoriaLogsClient,
)
