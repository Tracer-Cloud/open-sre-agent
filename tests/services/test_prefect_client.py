from __future__ import annotations

from typing import Any
import pytest
from pydantic import BaseModel, ValidationError

from app.services.prefect.client import make_prefect_client
from app.services._base import ServiceClientUnavailable


class _DummyModel(BaseModel):
    x: int


def _create_validation_error() -> ValidationError:
    try:
        _DummyModel(x="not an int")  # type: ignore[arg-type]
    except ValidationError as e:
        return e
    raise RuntimeError("unreachable")


def test_make_prefect_client_raises_validation_error_on_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise _create_validation_error()

    monkeypatch.setattr("app.services.prefect.client.PrefectConfig", _raise)
    with pytest.raises(ValidationError):
        make_prefect_client(api_key="key", api_url="url")


def test_make_prefect_client_raises_unavailable_on_other_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("unexpected")

    monkeypatch.setattr("app.services.prefect.client.PrefectConfig", _raise)
    with pytest.raises(ServiceClientUnavailable):
        make_prefect_client(api_key="key", api_url="url")
