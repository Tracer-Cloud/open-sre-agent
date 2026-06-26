"""Grafana Tempo integration verifier."""

from __future__ import annotations

from integrations.tempo import build_tempo_config, validate_tempo_config
from integrations.verification import register_validation_verifier

verify_tempo = register_validation_verifier(
    "tempo",
    build_config=build_tempo_config,
    validate_config=validate_tempo_config,
)
