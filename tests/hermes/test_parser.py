"""Tests for :mod:`app.hermes.parser`."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.hermes.incident import LogLevel
from app.hermes.parser import parse_log_line


class TestParseLogLine:
    def test_parses_run_id_header(self) -> None:
        line = (
            "2026-05-12 00:12:17,243 ERROR [20260512_001202_1bab8f] "
            "tools.terminal_tool: terminal_tool exception:"
        )
        record = parse_log_line(line)

        assert record is not None
        assert record.timestamp == datetime(2026, 5, 12, 0, 12, 17, 243000)
        assert record.level is LogLevel.ERROR
        assert record.run_id == "20260512_001202_1bab8f"
        assert record.logger == "tools.terminal_tool"
        assert record.message == "terminal_tool exception:"
        assert record.is_continuation is False

    def test_parses_warning_without_run_id(self) -> None:
        line = (
            "2026-05-11 23:31:15,063 WARNING gateway.platforms.telegram_network: "
            "[Telegram] Primary api.telegram.org connection failed"
        )
        record = parse_log_line(line)

        assert record is not None
        assert record.level is LogLevel.WARNING
        assert record.logger == "gateway.platforms.telegram_network"
        assert record.run_id is None
        assert record.message.startswith("[Telegram] Primary api.telegram.org")

    def test_blank_line_returns_none(self) -> None:
        assert parse_log_line("") is None
        assert parse_log_line("\n") is None
        assert parse_log_line("   \n") is not None

    def test_continuation_inherits_prev_level(self) -> None:
        line = '  File "/Users/dev/.hermes/hermes-agent/tools/terminal_tool.py", line 1808'
        record = parse_log_line(line, prev_level=LogLevel.ERROR)

        assert record is not None
        assert record.is_continuation is True
        assert record.level is LogLevel.ERROR
        assert record.logger == ""
        assert record.message == line

    def test_continuation_defaults_to_error_when_no_prev_level(self) -> None:
        record = parse_log_line("Traceback (most recent call last):")

        assert record is not None
        assert record.is_continuation is True
        # Defensive default so a multi-line payload that happens to be the
        # very first line we ever see still surfaces as a high-severity
        # signal rather than disappearing into DEBUG noise.
        assert record.level is LogLevel.ERROR

    def test_unknown_level_treated_as_continuation(self) -> None:
        line = "2026-05-12 00:00:00,000 NOTICE foo.bar: msg"
        record = parse_log_line(line, prev_level=LogLevel.WARNING)

        assert record is not None
        assert record.is_continuation is True

    def test_strips_carriage_return(self) -> None:
        line = "2026-05-12 00:00:00,000 INFO foo.bar: ok\r"
        record = parse_log_line(line)

        assert record is not None
        assert record.raw == "2026-05-12 00:00:00,000 INFO foo.bar: ok"

    @pytest.mark.parametrize(
        "level_name",
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    def test_recognizes_all_standard_levels(self, level_name: str) -> None:
        record = parse_log_line(f"2026-05-12 00:00:00,000 {level_name} foo.bar: hi")

        assert record is not None
        assert record.level is LogLevel(level_name)
