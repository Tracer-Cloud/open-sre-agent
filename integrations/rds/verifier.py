"""RDS integration verifier."""

from __future__ import annotations

from integrations.rds import RDSConfig
from integrations.rds.client import RDSClient
from integrations.verification import register_probe_verifier

verify_rds = register_probe_verifier(
    "rds",
    config=RDSConfig.model_validate,
    client=RDSClient,
)
