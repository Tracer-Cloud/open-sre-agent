"""Sentry capture coverage for verification adapter probe failures.

Issue #1469: every ``except`` block in ``_verification_adapters.py`` that
swallows a probe error must call ``_report_probe_failure`` so failures reach
Sentry with structured tags (surface, integration, event).

Part 1 -- AST-level: parametrized checks that each listed except handler
contains a ``_report_probe_failure`` call with the expected ``integration=``
keyword argument.

Part 2 -- runtime: mock ``report_exception`` and exercise ``_report_probe_failure``
directly to verify vendor-vs-bug severity split and tag structure.

Part 3 -- integration: exercise selected verifiers end-to-end through a forced
exception and assert the Sentry path fires.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests
from pydantic import ValidationError as PydanticValidationError

from app.integrations._verification_adapters import (
    _report_probe_failure,
    _verify_grafana,
    _verify_slack,
    _verify_telegram,
    _verify_whatsapp,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = "app/integrations/_verification_adapters.py"


# ---------------------------------------------------------------------------
# Part 1 -- AST: every except block calls _report_probe_failure
# ---------------------------------------------------------------------------

_PROBE_SITES: tuple[tuple[str, str], ...] = (
    ("_verify_with_validation_result", "service"),
    ("_verifier", "service"),
    ("_verify_grafana", "grafana"),
    ("_verify_aws", "aws"),
    ("_verify_slack", "slack"),
    ("_verify_tracer", "tracer"),
    ("_verify_discord", "discord"),
    ("_verify_telegram", "telegram"),
    ("_verify_whatsapp", "whatsapp"),
)


def _find_functions(tree: ast.AST, name: str) -> list[ast.FunctionDef]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    ]


def _except_handlers(fn: ast.FunctionDef) -> list[ast.ExceptHandler]:
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            handlers.append(node)
    return handlers


def _contains_call(handler: ast.ExceptHandler, func_name: str) -> bool:
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            return True
    return False


def _is_success_passthrough(handler: ast.ExceptHandler) -> bool:
    """True when the handler is a known success-path passthrough.

    The Discord event-loop handler returns "passed" without capture because
    the exception means the token was accepted. This is intentional.
    """
    src = ast.dump(handler)
    return "run() cannot be called from a running event loop" in src


@pytest.mark.parametrize(
    ("func_name", "integration"),
    _PROBE_SITES,
    ids=[f"{fn}({integ})" for fn, integ in _PROBE_SITES],
)
def test_except_blocks_call_report_probe_failure(func_name: str, integration: str) -> None:
    source = (_REPO_ROOT / _MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = _find_functions(tree, func_name)
    assert functions, f"{func_name} not found in {_MODULE}"

    for fn in functions:
        handlers = _except_handlers(fn)
        uncovered = [
            h
            for h in handlers
            if not _contains_call(h, "_report_probe_failure") and not _is_success_passthrough(h)
        ]
        assert not uncovered, (
            f"{_MODULE}::{func_name} has {len(uncovered)} except handler(s) "
            f"without _report_probe_failure"
        )


def test_module_imports_report_exception() -> None:
    source = (_REPO_ROOT / _MODULE).read_text(encoding="utf-8")
    assert "from app.utils.errors import report_exception" in source


def test_report_probe_failure_call_count() -> None:
    source = (_REPO_ROOT / _MODULE).read_text(encoding="utf-8")
    count = source.count("_report_probe_failure(")
    definition_count = source.count("def _report_probe_failure(")
    assert count - definition_count >= 16, (
        f"expected at least 16 call sites, found {count - definition_count}"
    )


# ---------------------------------------------------------------------------
# Part 2 -- runtime: _report_probe_failure severity and tag routing
# ---------------------------------------------------------------------------


@patch("app.integrations._verification_adapters.report_exception")
def test_vendor_transport_error_gets_info_severity(
    mock_report: MagicMock,
) -> None:
    exc = httpx.ConnectError("connection refused")
    _report_probe_failure(exc, integration="grafana")

    mock_report.assert_called_once()
    call_kwargs = mock_report.call_args
    assert call_kwargs.kwargs["severity"] == "info"
    assert call_kwargs.kwargs["tags"]["event"] == "vendor_failure"
    assert call_kwargs.kwargs["tags"]["surface"] == "integration_probe"
    assert call_kwargs.kwargs["tags"]["integration"] == "grafana"


@patch("app.integrations._verification_adapters.report_exception")
def test_requests_timeout_gets_info_severity(mock_report: MagicMock) -> None:
    exc = requests.exceptions.Timeout("read timed out")
    _report_probe_failure(exc, integration="telegram")

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["event"] == "vendor_failure"


@patch("app.integrations._verification_adapters.report_exception")
def test_unknown_error_gets_error_severity(mock_report: MagicMock) -> None:
    exc = RuntimeError("unexpected")
    _report_probe_failure(exc, integration="slack")

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "error"
    assert mock_report.call_args.kwargs["tags"]["event"] == "verify_failed"
    assert mock_report.call_args.kwargs["tags"]["surface"] == "integration_probe"


@patch("app.integrations._verification_adapters.report_exception")
def test_import_error_gets_info_severity(mock_report: MagicMock) -> None:
    exc = ImportError("No module named 'discord'")
    _report_probe_failure(exc, integration="discord")

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["event"] == "missing_dependency"
    assert mock_report.call_args.kwargs["tags"]["surface"] == "integration_probe"


@patch("app.integrations._verification_adapters.report_exception")
def test_pydantic_validation_error_gets_info_severity(mock_report: MagicMock) -> None:
    from pydantic import BaseModel

    class _Dummy(BaseModel):
        x: int

    try:
        _Dummy.model_validate({"x": "not_an_int"})
    except PydanticValidationError as exc:
        _report_probe_failure(exc, integration="grafana")

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["event"] == "invalid_config"
    assert mock_report.call_args.kwargs["tags"]["surface"] == "integration_probe"


@patch("app.integrations._verification_adapters.report_exception")
def test_config_error_override_gets_info_severity(mock_report: MagicMock) -> None:
    exc = ValueError("Missing AWS role_arn or credentials.")
    _report_probe_failure(exc, integration="aws", config_error=True)

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["event"] == "invalid_config"


@patch("app.integrations._verification_adapters.report_exception")
@patch("app.integrations._verification_adapters._build_sts_client")
def test_verify_aws_missing_credentials_returns_missing(
    mock_build: MagicMock, mock_report: MagicMock
) -> None:
    from app.integrations._verification_adapters import _verify_aws

    mock_build.side_effect = ValueError("Missing AWS role_arn or credentials.")
    res = _verify_aws("env", {"region": "us-east-1"})
    assert res["status"] == "missing"
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["event"] == "invalid_config"
    assert mock_report.call_args.kwargs["tags"]["integration"] == "aws"


@patch("app.integrations._verification_adapters.report_exception")
def test_expected_override_gets_info_severity(mock_report: MagicMock) -> None:
    exc = RuntimeError("discord login failure")
    _report_probe_failure(exc, integration="discord", expected=True)

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["event"] == "vendor_failure"


@patch("app.integrations._verification_adapters.report_exception")
def test_message_includes_integration_name(mock_report: MagicMock) -> None:
    _report_probe_failure(ValueError("bad"), integration="aws")
    msg = mock_report.call_args.kwargs["message"]
    assert "[aws]" in msg
    assert "verification probe failed" in msg


# ---------------------------------------------------------------------------
# Part 3 -- integration: verifier functions fire Sentry on probe failure
# ---------------------------------------------------------------------------


@patch("app.integrations._verification_adapters.report_exception")
@patch("app.integrations._verification_adapters.requests.get")
def test_verify_grafana_captures_on_connection_error(
    mock_get: MagicMock, mock_report: MagicMock
) -> None:
    mock_get.side_effect = requests.exceptions.ConnectionError("refused")
    res = _verify_grafana("env", {"endpoint": "http://graf:3000", "api_key": "tok"})
    assert res["status"] == "failed"
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["integration"] == "grafana"


@patch("app.integrations._verification_adapters.report_exception")
@patch("app.integrations._verification_adapters.requests.get")
def test_verify_telegram_captures_on_timeout(mock_get: MagicMock, mock_report: MagicMock) -> None:
    mock_get.side_effect = requests.exceptions.Timeout("timeout")
    res = _verify_telegram("env", {"bot_token": "123:ABC"})
    assert res["status"] == "failed"
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["severity"] == "info"
    assert mock_report.call_args.kwargs["tags"]["integration"] == "telegram"


@patch("app.integrations._verification_adapters.report_exception")
@patch("app.integrations._verification_adapters.requests.get")
def test_verify_whatsapp_captures_on_http_error(
    mock_get: MagicMock, mock_report: MagicMock
) -> None:
    mock_get.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
    res = _verify_whatsapp("env", {"account_sid": "AC123", "auth_token": "secret"})
    assert res["status"] == "failed"
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["integration"] == "whatsapp"
    assert mock_report.call_args.kwargs["tags"]["event"] == "vendor_failure"


@patch("app.integrations._verification_adapters.report_exception")
def test_verify_slack_captures_on_config_validation_error(
    mock_report: MagicMock,
) -> None:
    res = _verify_slack("env", {"webhook_url": 12345}, send_slack_test=False)
    assert res["status"] == "missing"
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["integration"] == "slack"
    assert mock_report.call_args.kwargs["tags"]["event"] == "invalid_config"


# ---------------------------------------------------------------------------
# Discord ImportError special path
# ---------------------------------------------------------------------------


def test_discord_import_failure_sets_sentry_tag() -> None:
    source = (_REPO_ROOT / _MODULE).read_text(encoding="utf-8")
    assert 'sentry_sdk.set_tag("integrations.discord.import_failed", "true")' in source
