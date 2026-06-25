"""Alertmanager integration verifier.

Registered with the central plugin registry at import time. The loader
at ``integrations/_verifiers_loader.py`` is the single place that
imports this module to trigger the registration.
"""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from services.alertmanager import AlertmanagerClient, AlertmanagerConfig

verify_alertmanager = register_probe_verifier(
    "alertmanager",
    config=AlertmanagerConfig.model_validate,
    client=AlertmanagerClient,
)
