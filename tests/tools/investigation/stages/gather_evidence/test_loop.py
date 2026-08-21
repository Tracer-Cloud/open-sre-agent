from core.llm.types import ToolCall
from tools.investigation.stages.gather_evidence.loop import tool_call_signature


class Unhashable:
    __hash__ = None  # type: ignore

    def __init__(self, val):
        self.val = val

    def __str__(self):
        return f"Unhashable({self.val})"


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


def test_tool_call_signature_unhashable_leaves():
    obj1 = Unhashable(1)
    tc1 = ToolCall(id="1", name="my_tool", input={"a": obj1})
    tc2 = ToolCall(id="2", name="my_tool", input={"a": obj1})
    assert tool_call_signature(tc1) == tool_call_signature(tc2)
    assert str(obj1) in str(tool_call_signature(tc1))


def test_tool_call_signature_container_collision():
    tc1 = ToolCall(id="1", name="my_tool", input={"filter": {}})
    tc2 = ToolCall(id="2", name="my_tool", input={"filter": []})
    assert tool_call_signature(tc1) != tool_call_signature(tc2)
