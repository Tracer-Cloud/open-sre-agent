"""Configurator handler for the Jenkins integration."""

from __future__ import annotations

from integrations.jenkins.setup import JENKINS_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_jenkins() -> tuple[str, str]:
    return configure_from_spec(JENKINS_SETUP, title="Jenkins")
