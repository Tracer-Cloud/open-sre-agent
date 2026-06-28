"""Ensure AgentState slices and AgentStateModel stay in sync.

If this test fails, a field was added/removed in one definition but not the other.
Fix drift by updating ``AgentStateModel`` and the matching slice in
``context/state/runtime_slices.py`` or ``context/state/slices.py``.
"""

from context.state.models import AgentState, AgentStateModel
from context.state.runtime_slices import (
    AlertInputSlice,
    DeliveryContextSlice,
    DeliveryOutputSlice,
    DiagnosisSlice,
    EvalHarnessSlice,
    InvestigationPlanSlice,
    InvestigationRuntimeSlice,
    MaskingSlice,
    SessionContext,
)
from context.state.slices import ChatStateSlice

_SLICE_TYPES: tuple[type, ...] = (
    SessionContext,
    ChatStateSlice,
    AlertInputSlice,
    InvestigationPlanSlice,
    InvestigationRuntimeSlice,
    DiagnosisSlice,
    MaskingSlice,
    DeliveryContextSlice,
    DeliveryOutputSlice,
    EvalHarnessSlice,
)


def _typed_dict_keys(td: type) -> set[str]:
    """Return all annotated keys from a TypedDict (including inherited ones)."""
    keys: set[str] = set()
    for base in reversed(td.__mro__):
        keys.update(getattr(base, "__annotations__", {}).keys())
    return keys


def _pydantic_keys(model: type) -> set[str]:
    """Return all field names from a Pydantic model, resolving aliases back to field names."""
    keys: set[str] = set()
    for name, field_info in model.model_fields.items():
        alias = field_info.alias
        keys.add(alias if alias is not None else name)
    return keys


def _slice_keys() -> set[str]:
    keys: set[str] = set()
    for slice_type in _SLICE_TYPES:
        keys.update(_typed_dict_keys(slice_type))
    return keys


def test_slices_cover_agent_state_keys() -> None:
    """Every AgentState key must be declared on exactly one slice TypedDict."""
    agent_keys = _typed_dict_keys(AgentState)
    slice_keys = _slice_keys()

    only_in_agent = agent_keys - slice_keys
    only_in_slices = slice_keys - agent_keys

    assert not only_in_agent, (
        f"Fields on AgentState but missing from slice TypedDicts: {sorted(only_in_agent)}"
    )
    assert not only_in_slices, (
        f"Fields on slice TypedDicts but missing from AgentState: {sorted(only_in_slices)}"
    )


def test_agent_state_and_model_share_same_keys() -> None:
    """AgentState and AgentStateModel must declare exactly the same set of field keys."""
    typed_dict_keys = _typed_dict_keys(AgentState)
    pydantic_keys = _pydantic_keys(AgentStateModel)

    only_in_typed_dict = typed_dict_keys - pydantic_keys
    only_in_pydantic = pydantic_keys - typed_dict_keys

    assert not only_in_typed_dict, (
        f"Fields present in AgentState (TypedDict) but missing from AgentStateModel: "
        f"{sorted(only_in_typed_dict)}"
    )
    assert not only_in_pydantic, (
        f"Fields present in AgentStateModel (Pydantic) but missing from AgentState: "
        f"{sorted(only_in_pydantic)}"
    )


def test_slice_types_do_not_duplicate_keys() -> None:
    """Each field belongs to one slice — no duplicate declarations across slices."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for slice_type in _SLICE_TYPES:
        for key in _typed_dict_keys(slice_type):
            if key in seen:
                duplicates.append(f"{key!r} on {slice_type.__name__} and {seen[key]}")
            else:
                seen[key] = slice_type.__name__

    assert not duplicates, "Duplicate slice keys: " + "; ".join(duplicates)
