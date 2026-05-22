"""Validation test utilities."""

from pydantic import BaseModel, ValidationError


class _DummyModel(BaseModel):
    x: int


def create_validation_error() -> ValidationError:
    """Create a sample Pydantic ValidationError for testing."""
    try:
        _DummyModel(x="not an int")  # type: ignore[arg-type]
    except ValidationError as e:
        return e
    raise RuntimeError("unreachable")
