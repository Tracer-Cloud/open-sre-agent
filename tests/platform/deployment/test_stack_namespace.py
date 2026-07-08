"""Tests for per-developer stack namespace via OPENSRE_STACK_SUFFIX."""

from __future__ import annotations

import pytest

from platform.deployment.ecr_deploy.stack import (
    ECR_REPO_NAME,
    GATEWAY_CONTAINER_NAME,
    STACK_NAME,
    WEB_CONTAINER_NAME,
    get_stack,
)


def test_get_stack_returns_defaults_when_no_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_STACK_SUFFIX", raising=False)

    stack = get_stack()

    assert stack.stack_name == STACK_NAME
    assert stack.ecr_repo_name == ECR_REPO_NAME
    assert stack.web_container_name == WEB_CONTAINER_NAME
    assert stack.gateway_container_name == GATEWAY_CONTAINER_NAME


def test_get_stack_applies_suffix_to_all_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_STACK_SUFFIX", "joe")

    stack = get_stack()

    assert stack.stack_name == f"{STACK_NAME}-joe"
    assert stack.ecr_repo_name == f"{ECR_REPO_NAME}-joe"
    assert stack.web_container_name == f"{WEB_CONTAINER_NAME}-joe"
    assert stack.gateway_container_name == f"{GATEWAY_CONTAINER_NAME}-joe"


def test_get_stack_strips_whitespace_from_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_STACK_SUFFIX", "  alice  ")

    stack = get_stack()

    assert stack.stack_name == f"{STACK_NAME}-alice"
    assert stack.ecr_repo_name == f"{ECR_REPO_NAME}-alice"


def test_get_stack_treats_blank_suffix_as_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_STACK_SUFFIX", "   ")

    stack = get_stack()

    assert stack.stack_name == STACK_NAME
    assert stack.ecr_repo_name == ECR_REPO_NAME


def test_get_stack_preserves_log_path_with_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_STACK_SUFFIX", "dev")

    stack = get_stack()

    assert stack.log_path == "/var/log/opensre-deploy.log"
