"""Configurator handler for the AWS integration."""

from __future__ import annotations

from integrations.aws.setup import AWS_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_aws() -> tuple[str, str]:
    return configure_from_spec(AWS_SETUP, title="AWS")
