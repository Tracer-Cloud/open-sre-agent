"""Vercel integration verifier."""

from __future__ import annotations

from app.integrations.verification import register_probe_verifier
from app.services.vercel.client import VercelClient, VercelConfig

verify_vercel = register_probe_verifier(
    "vercel",
    config=VercelConfig.model_validate,
    client=VercelClient,
)
