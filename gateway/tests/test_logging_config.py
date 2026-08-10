from __future__ import annotations

import logging

import pytest

from config.constants.logging import LOG_LEVEL_ENV
from gateway.core.config.logging_config import (
    _GatewayLogFormatter,
    _GatewayProcessLogFilter,
    configure_logging,
)
from platform.logging import quiet_noisy_third_party_loggers


@pytest.fixture(autouse=True)
def _reset_root_logging() -> None:
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)
    for name in (
        "httpx",
        "httpcore",
        "openai",
        "mcp",
        "mcp.client.session",
        "gateway",
        "integrations.messaging_security",
    ):
        logging.getLogger(name).setLevel(logging.NOTSET)


def _clear_root_handlers() -> None:
    """Put the root logger back in its pre-boot state.

    The autouse fixture runs at setup, but pytest's logging plugin re-attaches
    its capture handlers before the test body runs — so a test that wants the
    "nothing has configured logging yet" branch has to clear them here, or
    ``configure_logging`` takes the already-configured path and the assertion
    silently measures something else.
    """
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)


def _make_record(*, name: str, level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_gateway_formatter_shortens_package_logger_names() -> None:
    formatter = _GatewayLogFormatter(fmt="%(name)s | %(message)s")
    record = _make_record(
        name="gateway.transports.telegram.inbound_handler",
        level=logging.INFO,
        message="turn complete",
    )
    assert formatter.format(record) == "gateway | turn complete"


def test_gateway_process_filter_hides_routine_authorized_audit_lines() -> None:
    log_filter = _GatewayProcessLogFilter()
    allowed = _make_record(
        name="integrations.messaging_security",
        level=logging.INFO,
        message="[messaging-audit] authorized=True reason=User is authorized",
    )
    denied = _make_record(
        name="integrations.messaging_security",
        level=logging.WARNING,
        message="[messaging-audit] authorized=False reason=denied",
    )

    assert log_filter.filter(allowed) is False
    assert log_filter.filter(denied) is True


def test_quiet_noisy_loggers_sets_warning_level() -> None:
    quiet_noisy_third_party_loggers()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("openai").level == logging.WARNING
    assert logging.getLogger("mcp.client.session").level == logging.ERROR


def test_the_gateway_still_boots_at_info_when_no_level_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The knob is additive: unset must leave the pinned default exactly as it was."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    _clear_root_handlers()

    configure_logging()

    assert logging.getLogger().level == logging.INFO


def test_a_configured_level_reaches_the_root_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this the quiet turn paths stay invisible in a running deployment."""
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    _clear_root_handlers()

    configure_logging()

    assert logging.getLogger().level == logging.DEBUG


def test_a_configured_level_applies_when_another_host_owns_the_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``basicConfig`` no-ops once handlers exist, which would silently eat the knob."""
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    _clear_root_handlers()
    root = logging.getLogger()
    root.addHandler(logging.NullHandler())
    root.setLevel(logging.WARNING)

    configure_logging()

    assert root.level == logging.DEBUG


def test_an_unreadable_level_falls_back_instead_of_refusing_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in a Helm value must not be the reason the gateway will not start."""
    monkeypatch.setenv(LOG_LEVEL_ENV, "VERBOSE")
    _clear_root_handlers()

    configure_logging()

    assert logging.getLogger().level == logging.INFO
