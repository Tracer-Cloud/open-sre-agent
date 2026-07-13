"""Fargate plan helpers — no AWS calls."""

from __future__ import annotations

import pytest

from platform.deployment.fargate import lifecycle
from platform.deployment.fargate.stack import describe_plan, get_stack, resolve_env_name


def test_resolve_env_name_aliases() -> None:
    assert resolve_env_name("staging") == "staging"
    assert resolve_env_name("stage") == "staging"
    assert resolve_env_name("prod") == "production"
    assert resolve_env_name("production") == "production"


def test_get_stack_secret_paths() -> None:
    stack = get_stack(env="staging", region="us-east-1")
    assert stack.cluster_name == "opensre-cluster-staging"
    assert stack.secret_id("database_url") == "/opensre/staging/database_url"
    plan_lines = describe_plan(stack)
    assert any("opensre-web-staging" in line for line in plan_lines)
    assert any("opensre-slack-gateway-staging" in line for line in plan_lines)


def test_deploy_refuses_without_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_FARGATE_CONFIRM", raising=False)
    assert lifecycle.deploy(env="staging") == 2


def test_plan_returns_zero() -> None:
    assert lifecycle.plan(env="staging") == 0
