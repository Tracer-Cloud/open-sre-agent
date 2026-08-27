"""Release-artifact smoke contract for dynamically bundled code and data."""

from __future__ import annotations

import json

from click.testing import CliRunner

from surfaces.cli.app import cli


def test_package_smoke_finds_essential_tools_and_skills() -> None:
    result = CliRunner().invoke(cli, ["_package-smoke"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["registered_tools"] >= 250
    assert payload["action_skills"] >= 5
    assert payload["integration_verifiers"] >= 60
    assert payload["planning_instructions"] == "ok"


def test_package_smoke_command_is_hidden_from_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "_package-smoke" not in result.output
