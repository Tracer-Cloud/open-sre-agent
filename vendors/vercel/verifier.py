"""Vercel integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from vendors.vercel.client import VercelClient, VercelConfig

verify_vercel = register_probe_verifier(
    "vercel",
    config=VercelConfig.model_validate,
    client=VercelClient,
)
