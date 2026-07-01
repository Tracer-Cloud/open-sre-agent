"""Tests for infra.deployment.remote.server utility functions."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from infra.deployment.remote.server import (
    _id_to_iso,
    _make_id,
    _safe_investigation_path,
    _save_investigation,
    _slugify,
)


def test_slugify_converts_text_to_url_safe_format() -> None:
    """Test that _slugify converts special characters to hyphens and lowercases text."""
    result = _slugify("CPU High Usage at 90%")
    assert result == "cpu-high-usage-at-90"


def test_slugify_handles_multiple_special_characters() -> None:
    """Test that consecutive special characters are collapsed to single hyphen."""
    result = _slugify("Error!!! Database---Failed")
    assert result == "error-database-failed"


def test_slugify_trims_hyphens_from_edges() -> None:
    """Test that leading/trailing hyphens are removed."""
    result = _slugify("---test-alert---")
    assert result == "test-alert"


def test_slugify_handles_empty_string() -> None:
    """Test that empty string produces empty result."""
    result = _slugify("")
    assert result == ""


def test_slugify_handles_whitespace_only() -> None:
    """Test that whitespace-only string produces empty result after stripping."""
    result = _slugify("   ")
    assert result == ""


def test_make_id_generates_timestamp_with_slug() -> None:
    """Test that _make_id combines timestamp with slugified alert name."""
    result = _make_id("Database Connection Failed")
    # Format: YYYYMMDD_HHMMSS-<suffix>_<slug>
    parts = result.split("_", maxsplit=2)
    assert len(parts) == 3
    assert len(parts[0]) == 8  # YYYYMMDD
    assert parts[0].isdigit()
    time_part, suffix = parts[1].split("-", maxsplit=1)
    assert len(time_part) == 6  # HHMMSS
    assert time_part.isdigit()
    assert len(suffix) == 8
    int(suffix, 16)
    assert parts[2] == "database-connection-failed"


def test_make_id_uses_investigation_fallback_for_empty_alert_name() -> None:
    """Test that empty alert name uses 'investigation' as fallback slug."""
    result = _make_id("")
    # Format: YYYYMMDD_HHMMSS-<suffix>_investigation
    parts = result.split("_", maxsplit=2)
    assert len(parts) == 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    time_part, suffix = parts[1].split("-", maxsplit=1)
    assert len(time_part) == 6 and time_part.isdigit()
    assert len(suffix) == 8
    int(suffix, 16)
    assert parts[2] == "investigation"


def test_make_id_uses_investigation_fallback_for_whitespace_only() -> None:
    """Test that whitespace-only alert name uses 'investigation' as fallback."""
    result = _make_id("   ")
    parts = result.split("_", maxsplit=2)
    assert len(parts) == 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    time_part, suffix = parts[1].split("-", maxsplit=1)
    assert len(time_part) == 6 and time_part.isdigit()
    assert len(suffix) == 8
    int(suffix, 16)
    assert parts[2] == "investigation"


def test_make_id_handles_special_characters_in_alert_name() -> None:
    """Test that special characters in alert name are properly slugified."""
    result = _make_id("API!!! Latency---High")
    parts = result.split("_", maxsplit=2)
    assert len(parts) == 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    time_part, suffix = parts[1].split("-", maxsplit=1)
    assert len(time_part) == 6 and time_part.isdigit()
    assert len(suffix) == 8
    int(suffix, 16)
    assert parts[2] == "api-latency-high"


def test_make_id_truncates_long_slugs() -> None:
    """Test that very long alert names are truncated to 60 characters in slug."""
    long_name = "Error " * 50  # Creates a very long string
    result = _make_id(long_name)
    # Format is YYYYMMDD_HHMMSS-<suffix>_<slug>; the slug is last.
    parts = result.split("_", 2)
    slug = parts[2]
    assert len(slug) <= 60


def test_make_id_appends_uniqueness_suffix() -> None:
    """The timestamp segment includes 8 hex chars so same-second writes never collide."""
    result = _make_id("High CPU")
    timestamp_segment = result.split("_", maxsplit=2)[1]
    _time_part, suffix = timestamp_segment.split("-", maxsplit=1)
    assert len(suffix) == 8
    int(suffix, 16)  # raises if not hex


def test_make_id_is_unique_across_same_second_calls() -> None:
    """Regression: two ids for the same alert name must never be equal.

    Previously the id was ``YYYYMMDD_HHMMSS_<slug>`` with no random component,
    so back-to-back calls in the same second collided and the second
    investigation silently overwrote the first persisted report.
    """
    ids = {_make_id("High CPU") for _ in range(1000)}
    assert len(ids) == 1000


def test_list_investigations_uses_clean_slug_when_id_has_uniqueness_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pathlib import Path

    from infra.deployment.remote import server as remote_server

    monkeypatch.setattr(remote_server, "INVESTIGATIONS_DIR", Path(str(tmp_path)))

    inv_id = "20260628_123456-ab12cd34_cpu-spike-deploy"
    _save_investigation(
        inv_id=inv_id,
        alert_name="CPU Spike Deploy",
        pipeline_name="checkout",
        severity="critical",
        result=_result("root cause"),
    )

    item = remote_server.list_investigations()[0]

    assert item.id == inv_id
    assert item.alert_name == "cpu spike deploy"


def test_safe_investigation_path_accepts_valid_id() -> None:
    """Test that valid IDs are accepted and return a Path."""
    result = _safe_investigation_path("abc-123")
    assert result.name == "abc-123.md"


def test_safe_investigation_path_rejects_path_traversal_dotdot() -> None:
    """Test that ../x returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("../x")
    assert exc_info.value.status_code == 400
    assert "Invalid investigation ID" in exc_info.value.detail


def test_safe_investigation_path_rejects_x_dotdot() -> None:
    """Test that x/.. returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x/..")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_x_md() -> None:
    """Test that x.md returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x.md")
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "invalid_id",
    [
        pytest.param("x\n", id="trailing_newline"),
        pytest.param("\nvalid-id", id="newline_prefix"),
        pytest.param("valid\nid", id="embedded_newline"),
        pytest.param("x\n\n", id="multiple_newlines"),
        pytest.param("x\r\n", id="CRLF_line_ending"),
        pytest.param("x\r", id="carriage_return"),
        pytest.param("\n", id="only_newline"),
        pytest.param("\r\n", id="only_CRLF"),
        pytest.param("x\x00", id="null_byte"),
        pytest.param("x\u2028", id="unicode_line_separator"),
        pytest.param("x\u2029", id="unicode_paragraph_separator"),
    ],
)
def test_safe_investigation_path_rejects_newline_variants(invalid_id: str) -> None:
    """Test that IDs with newline/injection characters return 400 Invalid investigation ID.

    Covers path traversal and injection attack vectors including:
    - Newline characters (LF, CRLF, CR)
    - Null bytes
    - Unicode line/paragraph separators
    """
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path(invalid_id)
    assert exc_info.value.status_code == 400
    assert "Invalid investigation ID" in exc_info.value.detail


def test_safe_investigation_path_rejects_empty() -> None:
    """Test that empty ID returns 400 Invalid investigation ID."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_special_chars() -> None:
    """Test that IDs with special characters return 400."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path("x$y")
    assert exc_info.value.status_code == 400


def test_safe_investigation_path_rejects_single_dot() -> None:
    """Test that single dot in ID returns 400."""
    with pytest.raises(HTTPException) as exc_info:
        _safe_investigation_path(".")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Investigation ID uniqueness + atomic persistence (regression coverage).
# ---------------------------------------------------------------------------


def _result(root_cause: str) -> dict[str, str]:
    return {"root_cause": root_cause, "report": "r", "problem_md": "pm"}


def test_save_investigation_does_not_overwrite_same_second_same_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Regression: two same-name investigations in the same second must both persist.

    Before the uniqueness suffix, both calls produced an identical id and the
    second ``_save_investigation`` silently overwrote the first ``.md``, losing
    the earlier root cause. With the suffix each id maps to its own file.
    """
    from pathlib import Path

    from infra.deployment.remote import server as remote_server

    monkeypatch.setattr(remote_server, "INVESTIGATIONS_DIR", Path(str(tmp_path)))

    path_a = _save_investigation(
        inv_id=remote_server._make_id("DB Down"),
        alert_name="DB Down",
        pipeline_name="p",
        severity="high",
        result=_result("root cause A"),
    )
    path_b = _save_investigation(
        inv_id=remote_server._make_id("DB Down"),
        alert_name="DB Down",
        pipeline_name="p",
        severity="high",
        result=_result("root cause B"),
    )

    # Different ids → different files → no silent overwrite.
    assert path_a != path_b
    assert path_a.exists() and path_b.exists()
    assert "root cause A" in path_a.read_text(encoding="utf-8")
    assert "root cause B" in path_b.read_text(encoding="utf-8")


def test_save_investigation_is_atomic(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """A write must land fully or not at all — never a torn or empty ``.md``.

    Patches ``Path.write_text`` on the *temp* staging path to raise, simulating
    a crash mid-write. No report file (nor any leftover ``.tmp``) should remain.
    """
    from pathlib import Path

    from infra.deployment.remote import server as remote_server

    monkeypatch.setattr(remote_server, "INVESTIGATIONS_DIR", Path(str(tmp_path)))

    real_write_text = Path.write_text

    def fail_on_tmp(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> None:
        if self.name.endswith(".tmp"):
            raise OSError("simulated crash mid-write")
        real_write_text(self, data, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "write_text", fail_on_tmp)

    with pytest.raises(OSError):
        _save_investigation(
            inv_id=remote_server._make_id("Crash Test"),
            alert_name="Crash Test",
            pipeline_name="p",
            severity="high",
            result=_result("lost in crash"),
        )

    # No partial report and no stranded temp file leak into the investigations dir.
    base = Path(str(tmp_path))
    assert list(base.glob("*.md")) == []
    assert list(base.glob("*.tmp")) == []


def test_id_to_iso_parses_new_suffixed_id() -> None:
    """The ISO-8601 timestamp is still recoverable from the new id format."""
    iso = _id_to_iso("20260101_120000_db-down_deadbeef")
    assert iso.startswith("2026-01-01T12:00:00")


def test_id_to_iso_returns_empty_on_garbage() -> None:
    assert _id_to_iso("nonsense") == ""
