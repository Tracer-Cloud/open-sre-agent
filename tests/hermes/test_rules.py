"""Tests for the pattern-rule registry and its integration with the classifier."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from integrations.hermes.classifier import IncidentClassifier
from integrations.hermes.incident import IncidentSeverity, LogLevel, LogRecord
from integrations.hermes.rules import (
    PatternRule,
    RepeatRule,
    default_pattern_rules,
    evaluate_all,
)


def _rec(
    message: str,
    *,
    level: LogLevel = LogLevel.ERROR,
    logger_name: str = "hermes.agent",
    seconds: int = 0,
    is_continuation: bool = False,
) -> LogRecord:
    return LogRecord(
        timestamp=datetime(2026, 5, 12, 12, 0, 0) + timedelta(seconds=seconds),
        level=level,
        logger=logger_name,
        message=message,
        raw=f"{level.value} {logger_name}: {message}",
        is_continuation=is_continuation,
    )


def _message_variants(message: str) -> tuple[str, ...]:
    """Benign transforms that should not change rule match outcomes."""
    return (
        message,
        message.upper(),
        f"[ingest] {message}",
        f"{message} [trace_id=abc123]",
    )


@pytest.mark.parametrize(
    "rule_name,message",
    [
        ("oom_killed", "MemoryError: out of memory while allocating buffer"),
        ("oom_killed", "Killed process 12345 (python) total-vm:8192MB"),
        ("oom_killed", "cgroup out of memory: anon-rss:4096MB"),
        ("context_window_exceeded", "context_length_exceeded: 65798 tokens > 32768"),
        ("context_window_exceeded", "prompt is too long (78786 tokens)"),
        ("auth_failure", "401 Unauthorized: invalid api_key"),
        ("auth_failure", "Authentication failed: signature mismatch"),
        ("rate_limit", "429 Too Many Requests"),
        ("rate_limit", "Rate-limited by upstream provider"),
        ("database_wal_growth", "WAL backlog growing: 1284 MB"),
        ("database_wal_growth", "checkpoint is lagging behind by 5min"),
        ("deadlock", "deadlock detected, transaction rolled back"),
        ("disk_full", "no space left on device"),
        ("disk_full", "ENOSPC: cannot write"),
    ],
)
def test_default_pattern_rules_match_expected_messages(rule_name: str, message: str) -> None:
    rules = {r.name: r for r in default_pattern_rules()}
    rule = rules[rule_name]
    record = _rec(message)
    incident = rule.evaluate(record)
    assert incident is not None
    assert incident.rule == rule_name


@pytest.mark.parametrize(
    "rule_name,message",
    [
        ("oom_killed", "MemoryError: out of memory while allocating buffer"),
        ("context_window_exceeded", "context_length_exceeded: 65798 tokens > 32768"),
        ("auth_failure", "401 Unauthorized: invalid api_key"),
        ("rate_limit", "429 Too Many Requests"),
        ("database_wal_growth", "WAL backlog growing: 1284 MB"),
        ("deadlock", "deadlock detected, transaction rolled back"),
        ("disk_full", "no space left on device"),
    ],
)
def test_default_pattern_rules_match_metamorphic_message_variants(
    rule_name: str, message: str
) -> None:
    rules = {r.name: r for r in default_pattern_rules()}
    rule = rules[rule_name]
    for variant in _message_variants(message):
        incident = rule.evaluate(_rec(variant))
        assert incident is not None, f"rule={rule_name} failed for variant={variant!r}"
        assert incident.rule == rule_name


def test_pattern_rules_respect_min_level() -> None:
    rules = {r.name: r for r in default_pattern_rules()}
    rule = rules["oom_killed"]
    # Same message but at INFO level — should not fire.
    record = _rec("MemoryError: out of memory", level=LogLevel.INFO)
    assert rule.evaluate(record) is None


def test_pattern_rules_ignore_continuation_lines() -> None:
    rules = {r.name: r for r in default_pattern_rules()}
    rule = rules["oom_killed"]
    record = _rec("MemoryError", is_continuation=True)
    assert rule.evaluate(record) is None


def test_pattern_rule_does_not_match_logger_name_in_raw_line() -> None:
    """Regression: rule keywords appearing only in the logger prefix must
    not fire — patterns scan ``record.message`` exclusively.

    ``oom_killer_watcher`` contains ``oom_killer`` which *does* match the
    ``\\boom[- _]killer`` pattern when ``record.raw`` is searched, so this
    test would fail if the old ``or pattern.search(record.raw)`` guard
    were re-introduced.
    """
    rules = {r.name: r for r in default_pattern_rules()}
    rule = rules["oom_killed"]
    record = _rec(
        "heartbeat ok",
        level=LogLevel.ERROR,
        logger_name="oom_killer_watcher",
    )
    # Confirm the raw line actually triggers the pattern — ensures the
    # test is a genuine regression guard, not vacuously green. Looked
    # up by pattern source so reordering the tuple can't silently make
    # this assertion test the wrong pattern.
    oom_killer_pat = next(p for p in rule.patterns if r"oom[- _]killer" in p.pattern)
    assert oom_killer_pat.search(record.raw) is not None
    assert rule.evaluate(record) is None


def test_repeat_rule_only_fires_at_threshold() -> None:
    crash = next(r for r in default_pattern_rules() if r.name == "crash_loop")
    assert isinstance(crash, RepeatRule)
    # Two restarts: no incident
    assert crash.evaluate(_rec("agent restarting", level=LogLevel.WARNING)) is None
    assert crash.evaluate(_rec("agent restarted", level=LogLevel.WARNING, seconds=10)) is None
    # Third restart inside the 120s window → fires
    incident = crash.evaluate(_rec("process restarting", level=LogLevel.WARNING, seconds=20))
    assert incident is not None
    assert incident.rule == "crash_loop"
    assert incident.severity is IncidentSeverity.CRITICAL
    assert len(incident.records) == 3


def test_repeat_rule_crash_loop_ignores_info_level_restart_spam() -> None:
    """INFO startup lines must not count toward crash_loop — same min_level
    contract as :class:`PatternRule`."""
    crash = next(r for r in default_pattern_rules() if r.name == "crash_loop")
    assert isinstance(crash, RepeatRule)
    msg = "agent restarted after unexpected exit"
    for i in range(5):
        assert crash.evaluate(_rec(msg, level=LogLevel.INFO, seconds=i * 5)) is None


def test_repeat_rule_window_ages_out_old_hits() -> None:
    crash = next(r for r in default_pattern_rules() if r.name == "crash_loop")
    assert isinstance(crash, RepeatRule)
    crash.evaluate(_rec("agent restarting", level=LogLevel.WARNING, seconds=0))
    crash.evaluate(_rec("agent restarting", level=LogLevel.WARNING, seconds=10))
    # 200s later — first two have aged out, this is hit #1
    assert crash.evaluate(_rec("agent restarting", level=LogLevel.WARNING, seconds=200)) is None


def test_repeat_rule_does_not_match_logger_name_in_raw_line() -> None:
    """Regression: a keyword appearing only in ``record.raw`` (not the
    message) must not count toward the RepeatRule threshold.

    ``service restart`` as a logger name produces a raw line that matches
    ``(?:service)\\s+(?:re)?start`` — the crash-loop pattern. This test
    confirms that 5 such records with a benign message never fire an
    incident, and would fail if raw-matching were re-introduced.
    """
    crash = next(r for r in default_pattern_rules() if r.name == "crash_loop")
    assert isinstance(crash, RepeatRule)
    for i in range(5):
        record = LogRecord(
            timestamp=datetime(2026, 5, 12, 12, 0, 0) + timedelta(seconds=i * 5),
            level=LogLevel.WARNING,
            logger="service restart",
            message="heartbeat ok",
            raw="WARNING service restart: heartbeat ok",
        )
        # Confirm the raw line actually triggers the pattern. Looked up
        # by pattern source so reordering the tuple can't silently make
        # this assertion test the wrong pattern.
        restart_pat = next(p for p in crash.patterns if "start" in p.pattern)
        assert restart_pat.search(record.raw) is not None
        assert crash.evaluate(record) is None


def test_classifier_picks_up_default_pattern_rules() -> None:
    classifier = IncidentClassifier()
    record = _rec("MemoryError while allocating context buffer")
    incidents = classifier.observe(record)
    rules_fired = {i.rule for i in incidents}
    # Both structural error_severity and the oom_killed pattern fire.
    assert "error_severity" in rules_fired
    assert "oom_killed" in rules_fired


def test_classifier_can_disable_default_pattern_rules() -> None:
    classifier = IncidentClassifier(use_default_pattern_rules=False)
    record = _rec("MemoryError while allocating context buffer")
    rules_fired = {i.rule for i in classifier.observe(record)}
    assert "oom_killed" not in rules_fired
    assert "error_severity" in rules_fired


def test_classifier_accepts_custom_pattern_rule() -> None:
    import re

    custom = PatternRule(
        name="snowflake_outage",
        severity=IncidentSeverity.HIGH,
        title_template="Snowflake unreachable from {logger}",
        patterns=(re.compile(r"snowflake .* unreachable", re.IGNORECASE),),
        min_level=LogLevel.WARNING,
    )
    classifier = IncidentClassifier(use_default_pattern_rules=False, pattern_rules=[custom])
    record = _rec(
        "Snowflake compute warehouse unreachable after 5 retries",
        level=LogLevel.ERROR,
    )
    rules_fired = {i.rule for i in classifier.observe(record)}
    assert "snowflake_outage" in rules_fired


def test_evaluate_all_runs_full_pipeline_on_a_batch() -> None:
    records = [
        _rec("Starting agent", level=LogLevel.INFO),
        _rec("429 Too Many Requests from anthropic", level=LogLevel.WARNING),
        _rec("MemoryError", level=LogLevel.ERROR, seconds=5),
        _rec("no space left on device", level=LogLevel.CRITICAL, seconds=10),
    ]
    rules = default_pattern_rules()
    incidents = evaluate_all(rules, records)
    fired = {i.rule for i in incidents}
    assert {"rate_limit", "oom_killed", "disk_full"} <= fired
