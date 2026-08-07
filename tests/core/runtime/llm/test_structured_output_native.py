"""StructuredOutputClient native path is opt-in (default off)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from config.constants.llm import OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV
from core.llm.shared.structured_output import (
    StructuredOutputClient,
    json_schema_for_structured_output,
    native_structured_output_enabled,
)


class _TinySchema(BaseModel):
    root_cause_category: str = Field(description="category")
    note: str = ""


class _NativeBase:
    def __init__(self) -> None:
        self.prompt_invokes = 0
        self.native_invokes = 0

    def invoke_structured(self, model: type[BaseModel], _prompt: str) -> BaseModel:
        self.native_invokes += 1
        return model.model_validate({"root_cause_category": "pod_oomkilled", "note": "native"})

    def invoke(self, _prompt: str) -> Any:
        self.prompt_invokes += 1

        class _Resp:
            content = '{"root_cause_category": "code_defect", "note": "prompt"}'

        return _Resp()


class _FailingNativeBase:
    def __init__(self) -> None:
        self.native_invokes = 0

    def invoke_structured(self, _model: type[BaseModel], _prompt: str) -> BaseModel:
        self.native_invokes += 1
        raise RuntimeError("provider rejected output_config")

    def invoke(self, _prompt: str) -> Any:
        class _Resp:
            content = '{"root_cause_category": "code_defect", "note": "fallback"}'

        return _Resp()


def test_json_schema_sets_additional_properties_false() -> None:
    schema = json_schema_for_structured_output(_TinySchema)
    assert schema.get("additionalProperties") is False


def test_native_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, raising=False)
    assert native_structured_output_enabled() is False
    base = _NativeBase()
    result = StructuredOutputClient(base, _TinySchema).invoke("extract")
    assert result.note == "prompt"
    assert base.native_invokes == 0
    assert base.prompt_invokes == 1


def test_structured_output_uses_native_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, "1")
    base = _NativeBase()
    result = StructuredOutputClient(base, _TinySchema).invoke("extract")
    assert result.root_cause_category == "pod_oomkilled"
    assert result.note == "native"
    assert base.native_invokes == 1
    assert base.prompt_invokes == 0


def test_structured_output_falls_back_when_native_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, "1")
    base = _FailingNativeBase()
    result = StructuredOutputClient(base, _TinySchema).invoke("extract")
    assert result.root_cause_category == "code_defect"
    assert result.note == "fallback"
    assert base.native_invokes == 1
