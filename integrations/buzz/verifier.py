"""Buzz integration verifier."""

from __future__ import annotations

from integrations.buzz.client import BuzzClient
from integrations.config_models import BuzzConfig
from integrations.verification import register_probe_verifier

verify_buzz = register_probe_verifier(
    "buzz",
    config=BuzzConfig.model_validate,
    client=BuzzClient,
)
