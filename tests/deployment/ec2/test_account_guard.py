"""The deploy account guard aborts when the active AWS account is wrong."""

from __future__ import annotations

import pytest

from platform.deployment.aws import client as aws_client
from platform.deployment.aws.client import DeployAccountError, assert_deploy_account

_ENV = "OPENSRE_DEPLOY_ACCOUNT_ID"


class _FakeSts:
    def __init__(self, account: str) -> None:
        self._account = account

    def get_caller_identity(self) -> dict[str, str]:
        return {"Account": self._account}


@pytest.fixture
def stub_active_account(monkeypatch: pytest.MonkeyPatch):
    def _apply(active: str) -> None:
        monkeypatch.setattr(aws_client, "get_boto3_client", lambda *_a, **_k: _FakeSts(active))

    return _apply


def test_unset_env_skips_enforcement_without_sts_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: guard disabled (other devs / CI) — STS must not be called at all.
    monkeypatch.delenv(_ENV, raising=False)

    def _no_sts(*_args, **_kwargs):
        raise AssertionError("STS must not be called when the guard is disabled")

    monkeypatch.setattr(aws_client, "get_boto3_client", _no_sts)

    # Act / Assert: returns empty (no enforcement), never touches AWS.
    assert assert_deploy_account() == ""


def test_matching_account_passes(monkeypatch: pytest.MonkeyPatch, stub_active_account) -> None:
    # Arrange
    monkeypatch.setenv(_ENV, "111111111111")
    stub_active_account("111111111111")

    # Act / Assert
    assert assert_deploy_account() == "111111111111"


def test_wrong_account_aborts(monkeypatch: pytest.MonkeyPatch, stub_active_account) -> None:
    # Arrange: guard expects one account, credentials resolve to another.
    monkeypatch.setenv(_ENV, "111111111111")
    stub_active_account("222222222222")

    # Act / Assert
    with pytest.raises(DeployAccountError, match="not the configured"):
        assert_deploy_account()
