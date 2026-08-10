"""The one place a deployment says what its agent is called."""

from __future__ import annotations

import pytest

from config.constants.agent_identity import AGENT_NAME_ENV, agent_name


def test_an_unnamed_deployment_is_opensre() -> None:
    assert agent_name() == "OpenSRE"


def test_the_deployment_can_name_its_own_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AGENT_NAME_ENV, "AcmeOps")

    assert agent_name() == "AcmeOps"


def test_a_blank_name_falls_back_rather_than_leaving_the_agent_nameless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Helm value left empty must not produce "You are , an AI engineer"."""
    monkeypatch.setenv(AGENT_NAME_ENV, "   ")

    assert agent_name() == "OpenSRE"


def test_surrounding_whitespace_is_not_part_of_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AGENT_NAME_ENV, "  AcmeOps\n")

    assert agent_name() == "AcmeOps"
