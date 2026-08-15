"""AWS wizard: the role mode is gated on the ambient credential chain.

Assuming a role needs base credentials boto3 can find; on a bare laptop the
wizard used to collect the ARN and fail STS with "Unable to locate
credentials". The gate runs when "IAM Role ARN" is picked and offers the ways
forward instead.
"""

from __future__ import annotations

from typing import Any

import pytest

import surfaces.cli.wizard.configurators.aws as aws_configurator
from surfaces.cli.wizard._ui import WizardBack


@pytest.fixture
def quiet_console(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    printed: list[str] = []
    monkeypatch.setattr(
        aws_configurator._console, "print", lambda *args, **_kw: printed.append(str(args[0]))
    )
    return printed


def _pick(monkeypatch: pytest.MonkeyPatch, value: str) -> list[dict[str, Any]]:
    """Stub the wizard picker to answer *value* and record what was offered."""
    offered: list[dict[str, Any]] = []

    def _fake_choose(prompt: str, choices: list[Any], **kwargs: Any) -> str:
        offered.append({"prompt": prompt, "values": [c.value for c in choices], **kwargs})
        return value

    monkeypatch.setattr(aws_configurator, "_choose", _fake_choose)
    return offered


def test_role_mode_passes_through_when_ambient_credentials_exist(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    # Arrange — a profile / env keys / instance role is present
    monkeypatch.setattr(aws_configurator, "has_ambient_credentials", lambda: True)
    offered = _pick(monkeypatch, "unused")

    # Act
    mode = aws_configurator._gate_aws_mode(aws_configurator.ROLE_MODE)

    # Assert — no notice, no extra question
    assert mode == aws_configurator.ROLE_MODE
    assert offered == []
    assert quiet_console == []


def test_keys_mode_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — even with no ambient chain, static keys need no prerequisite
    monkeypatch.setattr(aws_configurator, "has_ambient_credentials", lambda: False)
    offered = _pick(monkeypatch, "unused")

    # Act
    mode = aws_configurator._gate_aws_mode(aws_configurator.KEYS_MODE)

    # Assert
    assert mode == aws_configurator.KEYS_MODE
    assert offered == []


def test_missing_chain_explains_and_steers_to_keys_by_default(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    # Arrange — bare laptop; user accepts the recommended way forward
    monkeypatch.setattr(aws_configurator, "has_ambient_credentials", lambda: False)
    offered = _pick(monkeypatch, aws_configurator.KEYS_MODE)

    # Act
    mode = aws_configurator._gate_aws_mode(aws_configurator.ROLE_MODE)

    # Assert — the user was told why, offered all three paths, and lands on keys
    assert mode == aws_configurator.KEYS_MODE
    assert any(aws_configurator.NO_AMBIENT_CREDENTIALS_NOTICE in line for line in quiet_console)
    assert offered[0]["default"] == aws_configurator.KEYS_MODE
    assert set(offered[0]["values"]) == {
        aws_configurator.KEYS_MODE,
        aws_configurator._CONFIGURE_ELSEWHERE,
        aws_configurator._CONTINUE_ROLE,
    }


def test_choosing_to_configure_credentials_first_leaves_setup_cleanly(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    """The wizard treats WizardBack as 'skipped' — no dead-end retry loop."""
    # Arrange
    monkeypatch.setattr(aws_configurator, "has_ambient_credentials", lambda: False)
    _pick(monkeypatch, aws_configurator._CONFIGURE_ELSEWHERE)

    # Act / Assert
    with pytest.raises(WizardBack):
        aws_configurator._gate_aws_mode(aws_configurator.ROLE_MODE)
    assert any(
        "aws configure" in line or "run setup" in line.lower() or "onboard" in line
        for line in quiet_console
    )


def test_user_may_insist_on_the_role_for_an_instance_role_deployment(
    monkeypatch: pytest.MonkeyPatch, quiet_console: list[str]
) -> None:
    # Arrange — user knows OpenSRE will run on EC2/ECS with an attached role
    monkeypatch.setattr(aws_configurator, "has_ambient_credentials", lambda: False)
    _pick(monkeypatch, aws_configurator._CONTINUE_ROLE)

    # Act
    mode = aws_configurator._gate_aws_mode(aws_configurator.ROLE_MODE)

    # Assert
    assert mode == aws_configurator.ROLE_MODE
