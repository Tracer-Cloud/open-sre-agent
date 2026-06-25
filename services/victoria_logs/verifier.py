"""Victoria Logs integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from services.victoria_logs import VictoriaLogsClient, VictoriaLogsConfig

verify_victoria_logs = register_probe_verifier(
    "victoria_logs",
    config=VictoriaLogsConfig.model_validate,
    client=VictoriaLogsClient,
)
