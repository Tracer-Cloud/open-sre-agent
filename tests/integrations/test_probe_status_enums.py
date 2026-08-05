from __future__ import annotations

from integrations.probes import ProbeResult, ProbeStatus


def test_probe_status_members_round_trip_from_string() -> None:
    assert set(ProbeStatus) == {
        ProbeStatus.PASSED,
        ProbeStatus.FAILED,
        ProbeStatus.MISSING,
    }
    assert ProbeStatus("passed") is ProbeStatus.PASSED
    assert ProbeStatus.PASSED == "passed"


def test_probe_result_ok_uses_enum_members() -> None:
    assert ProbeResult.passed("ok").ok is True
    assert ProbeResult.failed("bad").ok is False
    assert ProbeResult.missing("absent").ok is False


def test_probe_result_coerces_string_status() -> None:
    result = ProbeResult(status="failed", detail="config error")
    assert result.status is ProbeStatus.FAILED
    assert result.status == "failed"
