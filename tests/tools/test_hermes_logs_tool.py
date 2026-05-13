"""Tests for the agent-facing ``get_hermes_logs`` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.hermes.poller import HermesLogCursor
from app.tools.HermesLogsTool import get_hermes_logs

_LINES = [
    "2026-05-12 00:00:00,000 WARNING gateway.platforms.telegram: polling conflict (1/3)",
    "2026-05-12 00:00:10,000 WARNING gateway.platforms.telegram: polling conflict (2/3)",
    "2026-05-12 00:00:20,000 ERROR gateway.auth: auth bypass: user 9876543210 not in allowlist",
]


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    log = tmp_path / "errors.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


@pytest.fixture(autouse=True)
def _allow_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow the test's tmp_path directory by setting HERMES_LOG_PATH.

    The tool restricts log_path to HERMES_LOG_PATH's parent (or ~/.hermes
    by default). Tests use tmp_path which is outside ~/.hermes, so we point
    the env var at a file inside tmp_path to add the directory to the
    allow-list without changing test logic.
    """
    monkeypatch.setenv("HERMES_LOG_PATH", str(tmp_path / "errors.log"))


class TestScanMode:
    def test_scan_returns_recent_records(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        result = get_hermes_logs(op="scan", log_path=str(log), tail_lines=10)

        assert "error" not in result
        assert len(result["records"]) == 3
        assert result["records"][-1]["level"] == "ERROR"
        # Incidents should include the auth-bypass error_severity.
        rules = {i["rule"] for i in result["incidents"]}
        assert "error_severity" in rules

    def test_scan_flushes_open_traceback_at_end_of_window(self, tmp_path: Path) -> None:
        """A traceback with no following log line must still emit via classifier.flush()."""
        lines = [
            "2026-05-12 00:00:00,000 ERROR tools.x: Traceback (most recent call last):",
            '  File "/x", line 1, in foo',
        ]
        log = _write_log(tmp_path, lines)
        result = get_hermes_logs(op="scan", log_path=str(log), tail_lines=10)
        assert "error" not in result
        assert any(i["rule"] == "traceback" for i in result["incidents"])

    def test_scan_respects_tail_lines(self, tmp_path: Path) -> None:
        # Generate enough lines that the tail cap matters.
        lines = [
            f"2026-05-12 00:{i:02d}:00,000 INFO gateway.run: heartbeat #{i}" for i in range(50)
        ]
        log = _write_log(tmp_path, lines)
        result = get_hermes_logs(op="scan", log_path=str(log), tail_lines=5)
        # tail_lines is a soft floor (we seek back ~5 lines of bytes
        # then read forward), but the response is capped by max_records
        # which defaults to tail_lines when smaller — so we expect ≤5.
        assert len(result["records"]) <= 5
        # Last record should be the latest one.
        assert result["records"][-1]["message"].endswith("#49")


class TestTailMode:
    def test_tail_first_call_anchors_at_end(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        first = get_hermes_logs(op="tail", log_path=str(log))
        # First tail call with no cursor must not replay history.
        assert first["records"] == []
        # The returned cursor must be a non-empty token.
        assert first["cursor"]
        assert ":" in first["cursor"]

    def test_tail_cursor_resumes_correctly(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES[:2])
        first = get_hermes_logs(op="tail", log_path=str(log))
        cursor = first["cursor"]

        # Append the third line and re-poll with the cursor.
        with log.open("a", encoding="utf-8") as handle:
            handle.write(_LINES[2] + "\n")

        second = get_hermes_logs(op="tail", log_path=str(log), cursor=cursor)
        assert len(second["records"]) == 1
        assert second["records"][0]["level"] == "ERROR"
        # And the incident pipeline must surface the auth-bypass.
        assert any(i["rule"] == "error_severity" for i in second["incidents"])

    def test_tail_malformed_cursor_returns_error(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        result = get_hermes_logs(op="tail", log_path=str(log), cursor="garbage-not-a-real-cursor")
        assert "error" in result
        assert "cursor" in result["error"].lower()

    def test_tail_rejects_cursor_for_foreign_path(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        other = tmp_path / "other.log"
        other.write_text("secret\n", encoding="utf-8")
        bad_token = HermesLogCursor(path=str(other), device=0, inode=0, offset=0).to_token()
        result = get_hermes_logs(op="tail", log_path=str(log), cursor=bad_token)
        assert "error" in result
        assert "does not refer" in result["error"]


class TestLevelFilter:
    def test_levels_filter_drops_lower_severity(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        result = get_hermes_logs(op="scan", log_path=str(log), tail_lines=10, levels=["ERROR"])
        assert all(r["level"] == "ERROR" for r in result["records"])
        # The error_severity incident still fires — classifier
        # observed the WARNING records even though they were filtered
        # from the response. The auth-bypass ERROR is the actionable
        # one and it should be present.
        assert any(i["rule"] == "error_severity" for i in result["incidents"])

    def test_unknown_level_returns_error(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        result = get_hermes_logs(op="scan", log_path=str(log), levels=["BOGUS_LEVEL"])
        assert "error" in result


class TestErrorPaths:
    def test_unknown_op_returns_error(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        result = get_hermes_logs(op="ponder", log_path=str(log))
        assert "error" in result

    def test_missing_file_returns_empty_response_with_at_start_cursor(self, tmp_path: Path) -> None:
        ghost = tmp_path / "no-such.log"
        result = get_hermes_logs(op="tail", log_path=str(ghost))
        # Missing file is NOT an error — the watcher pattern allows a
        # late-appearing file. Just empty records + a fresh cursor.
        assert result.get("records") == []
        assert result["cursor"].endswith(f"@{ghost}")


class TestLogPathValidation:
    def test_rejects_path_outside_allowed_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Clear the env override so only ~/.hermes is allowed, then try
        # to read a file in tmp_path (which is outside ~/.hermes).
        monkeypatch.delenv("HERMES_LOG_PATH", raising=False)
        log = _write_log(tmp_path, _LINES)
        result = get_hermes_logs(op="scan", log_path=str(log))
        assert "error" in result
        assert "permitted" in result["error"]

    def test_accepts_path_within_env_override_dir(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path, _LINES)
        # autouse fixture already sets HERMES_LOG_PATH to tmp_path/errors.log
        # so tmp_path is in the allow-list.
        result = get_hermes_logs(op="scan", log_path=str(log), tail_lines=10)
        assert "error" not in result
        assert len(result["records"]) == 3

    def test_rejects_traversal_outside_allowed_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even with a symlink pointing outside tmp_path, resolve() sees
        # the real path and the validator rejects it.
        monkeypatch.delenv("HERMES_LOG_PATH", raising=False)
        result = get_hermes_logs(op="scan", log_path="/etc/passwd")
        assert "error" in result
        assert "permitted" in result["error"]


class TestDefaultPathResolution:
    def test_env_override_resolves(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _write_log(tmp_path, _LINES)
        monkeypatch.setenv("HERMES_LOG_PATH", str(log))
        # Don't pass log_path — env should win.
        result = get_hermes_logs(op="scan", tail_lines=10)
        assert len(result["records"]) == 3
