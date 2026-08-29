from dataclasses import dataclass

from core.llm.types import ToolCall
from tools.investigation.stages.gather_evidence.loop import (
    InvestigationLoopController,
    InvestigationToolCallCache,
    tool_call_signature,
)


@dataclass
class Unhashable:
    val: int

    def __str__(self) -> str:
        return f"Unhashable({self.val})"


class CountingFallback:
    def __init__(self, val: int) -> None:
        self.val = val
        self.str_calls = 0

    def __str__(self) -> str:
        self.str_calls += 1
        return f"CountingFallback({self.val})"


def test_tool_call_signature_same_args():
    tc1 = ToolCall(id="1", name="my_tool", input={"a": 1, "b": 2})
    tc2 = ToolCall(id="2", name="my_tool", input={"b": 2, "a": 1})
    assert tool_call_signature(tc1) == tool_call_signature(tc2)


def test_tool_call_signature_different_nested_values():
    tc1 = ToolCall(id="1", name="my_tool", input={"a": {"c": 3}})
    tc2 = ToolCall(id="2", name="my_tool", input={"a": {"c": 4}})
    assert tool_call_signature(tc1) != tool_call_signature(tc2)


def test_tool_call_signature_nested_dict():
    tc1 = ToolCall(id="1", name="my_tool", input={"a": {"z": 1, "y": 2}})
    tc2 = ToolCall(id="2", name="my_tool", input={"a": {"y": 2, "z": 1}})
    assert tool_call_signature(tc1) == tool_call_signature(tc2)


def test_tool_call_signature_list():
    tc1 = ToolCall(id="1", name="my_tool", input={"a": [1, 2, 3]})
    tc2 = ToolCall(id="2", name="my_tool", input={"a": [1, 2, 3]})
    tc3 = ToolCall(id="3", name="my_tool", input={"a": [1, 3, 2]})
    assert tool_call_signature(tc1) == tool_call_signature(tc2)
    assert tool_call_signature(tc1) != tool_call_signature(tc3)


def test_tool_call_signature_unhashable_leaves() -> None:
    obj1 = Unhashable(1)
    obj2 = Unhashable(1)
    tc1 = ToolCall(id="1", name="my_tool", input={"a": obj1})
    tc2 = ToolCall(id="2", name="my_tool", input={"a": obj2})
    assert tool_call_signature(tc1) == tool_call_signature(tc2)
    _ = hash(tool_call_signature(tc1))


def test_tool_call_signature_container_collision() -> None:
    tc1 = ToolCall(id="1", name="my_tool", input={"filter": {}})
    tc2 = ToolCall(id="2", name="my_tool", input={"filter": []})
    cache = InvestigationToolCallCache()
    cache.store(tool_call_signature(tc1), {"shape": "object"}, loop_iteration=0)

    assert cache.lookup(tool_call_signature(tc2)) is None


def test_tool_call_signature_distinguishes_signed_zero() -> None:
    positive = ToolCall(id="1", name="my_tool", input={"value": 0.0})
    negative = ToolCall(id="2", name="my_tool", input={"value": -0.0})
    cache = InvestigationToolCallCache()
    cache.store(tool_call_signature(positive), {"sign": "positive"}, loop_iteration=0)

    assert cache.lookup(tool_call_signature(negative)) is None


def test_tool_call_signature_normalizes_non_finite_floats() -> None:
    nan_1 = ToolCall(id="1", name="my_tool", input={"value": float("nan")})
    nan_2 = ToolCall(id="2", name="my_tool", input={"value": float("nan")})
    positive_inf = ToolCall(id="3", name="my_tool", input={"value": float("inf")})
    negative_inf = ToolCall(id="4", name="my_tool", input={"value": float("-inf")})

    assert tool_call_signature(nan_1) == tool_call_signature(nan_2)
    assert tool_call_signature(positive_inf) != tool_call_signature(negative_inf)


def test_tool_call_signature_preserves_scalar_types() -> None:
    signatures = {
        tool_call_signature(ToolCall(id="1", name="my_tool", input={"value": True})),
        tool_call_signature(ToolCall(id="2", name="my_tool", input={"value": 1})),
        tool_call_signature(ToolCall(id="3", name="my_tool", input={"value": 1.0})),
    }

    assert len(signatures) == 3


def test_controller_reuses_the_classified_signature_when_storing() -> None:
    leaf = CountingFallback(1)
    tool_call = ToolCall(id="call-1", name="my_tool", input={"value": leaf})
    controller = InvestigationLoopController(
        stagnation_nudge="try something else",
        checkpoint_nudge="checkpoint",
        max_stagnant_iterations=2,
    )

    controller.prepare_batch([tool_call], iteration=0)
    controller.record_runtime_result(
        iteration=0,
        tool_call_id=tool_call.id,
        result={"ok": True},
    )

    assert leaf.str_calls == 1
