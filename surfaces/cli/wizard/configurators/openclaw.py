"""Configurator handler for the OpenClaw MCP integration."""

from __future__ import annotations

from integrations.openclaw.setup import OPENCLAW_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_openclaw() -> tuple[str, str]:
    return configure_from_spec(OPENCLAW_SETUP, title="OpenClaw")
