"""Shared fixtures for deployment tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_deploy_account_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deploy tests must not depend on a developer's local account guard.

    ``OPENSRE_DEPLOY_ACCOUNT_ID`` is a local ``.env`` opt-in; when set it makes
    ``build_image``/``deploy`` verify the account via STS. Clear it so these
    tests exercise deploy logic without an AWS call. The guard has its own test.
    """
    monkeypatch.delenv("OPENSRE_DEPLOY_ACCOUNT_ID", raising=False)
