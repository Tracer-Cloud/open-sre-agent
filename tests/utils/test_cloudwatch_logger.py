from __future__ import annotations

from tests.utils.cloudwatch_logger import _log_group_prefix


def test_log_group_prefix_defaults_to_opensre(monkeypatch) -> None:
    monkeypatch.delenv("CLOUDWATCH_LOG_GROUP_PREFIX", raising=False)
    assert _log_group_prefix() == "/opensre/ai-investigations"


def test_log_group_prefix_respects_override(monkeypatch) -> None:
    monkeypatch.setenv("CLOUDWATCH_LOG_GROUP_PREFIX", "/custom/prefix/")
    assert _log_group_prefix() == "/custom/prefix"
