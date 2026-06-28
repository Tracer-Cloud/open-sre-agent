"""Tests for the requires_approval metadata on tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import EvidenceSource
from tools.base import BaseTool
from tools.registered_tool import RegisteredTool
from tools.tool_decorator import tool


class _ReadOnlyTool(BaseTool):
    """A tool that does not require approval (default)."""

    name = "read_only_tool"
    description = "A safe read-only tool"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    source: EvidenceSource = "storage"

    def run(self) -> dict[str, Any]:
        return {"status": "ok"}

    @classmethod
    def is_available(cls, sources: dict[str, dict]) -> bool:  # noqa: ARG003
        return True

    @classmethod
    def extract_params(cls, sources: dict[str, dict]) -> dict[str, Any]:  # noqa: ARG003
        return {}


class _DestructiveTool(BaseTool):
    """A tool that requires approval for messaging-origin invocations."""

    name = "destructive_tool"
    description = "A tool that writes to external systems"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    source: EvidenceSource = "github"
    requires_approval = True
    approval_reason = "This tool modifies external resources"

    def run(self) -> dict[str, Any]:
        return {"status": "modified"}

    @classmethod
    def is_available(cls, sources: dict[str, dict]) -> bool:  # noqa: ARG003
        return True

    @classmethod
    def extract_params(cls, sources: dict[str, dict]) -> dict[str, Any]:  # noqa: ARG003
        return {}


@tool(
    name="approval_function_tool",
    source="github",
    description="Function tool requiring approval",
    input_schema={"type": "object", "properties": {}},
    requires_approval=True,
    approval_reason="Needs approval",
    approval_scope="session",
    approval_expiry_seconds=60,
)
def approval_function_tool() -> dict[str, Any]:
    return {"ok": True}


class TestRequiresApprovalOnBaseTool:
    def test_default_requires_approval_is_false(self) -> None:
        tool_instance = _ReadOnlyTool()
        assert tool_instance.requires_approval is False
        assert tool_instance.approval_reason == ""

    def test_requires_approval_set_to_true(self) -> None:
        tool_instance = _DestructiveTool()
        assert tool_instance.requires_approval is True
        assert tool_instance.approval_reason == "This tool modifies external resources"


class TestRequiresApprovalOnRegisteredTool:
    def test_from_base_tool_carries_requires_approval(self) -> None:
        tool_instance = _DestructiveTool()
        registered = RegisteredTool.from_base_tool(tool_instance)
        assert registered.requires_approval is True
        assert registered.approval_reason == "This tool modifies external resources"

    def test_from_base_tool_default_no_approval(self) -> None:
        tool_instance = _ReadOnlyTool()
        registered = RegisteredTool.from_base_tool(tool_instance)
        assert registered.requires_approval is False
        assert registered.approval_reason == ""

    def test_from_function_carries_requires_approval_metadata(self) -> None:
        registered = approval_function_tool.__opensre_registered_tool__  # type: ignore[attr-defined]
        assert registered.requires_approval is True
        assert registered.approval_reason == "Needs approval"
        assert registered.approval_scope == "session"
        assert registered.approval_expiry_seconds == 60
