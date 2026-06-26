"""Jenkins integration verifier."""

from __future__ import annotations

from integrations.jenkins import build_jenkins_config, validate_jenkins_config
from integrations.verification import register_validation_verifier

verify_jenkins = register_validation_verifier(
    "jenkins",
    build_config=build_jenkins_config,
    validate_config=validate_jenkins_config,
)
