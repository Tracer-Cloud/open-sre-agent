"""Tests for the shared validate_classify() helper."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, field_validator

from integrations._classify_helpers import validate_classify


class _Simple(BaseModel):
    name: str
    value: int = 0

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v


# ---- happy path ---------------------------------------------------------------


def test_returns_config_and_key_on_valid_data() -> None:
    cfg, key = validate_classify(
        _Simple,
        "rec-1",
        {"name": "hello", "value": 42},
        integration="test",
        resolved_key="my_key",
    )
    assert key == "my_key"
    assert cfg is not None
    assert cfg.name == "hello"
    assert cfg.value == 42


# ---- validation failure -------------------------------------------------------


def test_returns_none_on_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations._classify_helpers.report_classify_failure",
        lambda *_a, **_kw: None,
    )
    cfg, key = validate_classify(
        _Simple,
        "rec-bad",
        {"name": ""},
        integration="test",
        resolved_key="my_key",
    )
    assert cfg is None
    assert key is None


def test_calls_report_classify_failure_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    reported: list[BaseException] = []

    monkeypatch.setattr(
        "integrations._classify_helpers.report_classify_failure",
        lambda exc, **_kw: reported.append(exc),
    )

    validate_classify(
        _Simple,
        "rec-err",
        {"name": ""},
        integration="test",
        resolved_key="my_key",
    )

    assert len(reported) == 1


# ---- check_fn (post-validation) -----------------------------------------------


def test_check_fn_rejects_when_false() -> None:
    cfg, key = validate_classify(
        _Simple,
        "rec-1",
        {"name": "ok"},
        integration="test",
        resolved_key="my_key",
        check_fn=lambda c: c.value > 0,
    )
    assert cfg is None
    assert key is None


def test_check_fn_accepts_when_true() -> None:
    cfg, key = validate_classify(
        _Simple,
        "rec-1",
        {"name": "ok", "value": 5},
        integration="test",
        resolved_key="my_key",
        check_fn=lambda c: c.value > 0,
    )
    assert cfg is not None
    assert key == "my_key"


# ---- pre_check (pre-validation guard) -----------------------------------------


def test_pre_check_skips_validation_when_false(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        "integrations._classify_helpers.report_classify_failure",
        lambda *_a, **_kw: called.append(True),
    )

    cfg, key = validate_classify(
        _Simple,
        "rec-1",
        {"name": "ok"},
        integration="test",
        resolved_key="my_key",
        pre_check=lambda _d: False,
    )

    assert cfg is None
    assert key is None
    assert not called


def test_pre_check_proceeds_when_true() -> None:
    cfg, key = validate_classify(
        _Simple,
        "rec-1",
        {"name": "ok"},
        integration="test",
        resolved_key="my_key",
        pre_check=lambda d: bool(d.get("name")),
    )
    assert cfg is not None
    assert key == "my_key"
