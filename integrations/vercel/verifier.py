"""Vercel integration verifier."""

from __future__ import annotations

from integrations.vercel.client import VercelClient, VercelConfig
from integrations.verification import register_probe_verifier

verify_vercel = register_probe_verifier(
    "vercel",
    config=VercelConfig.model_validate,
    client=VercelClient,
)
