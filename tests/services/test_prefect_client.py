from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.services._base import ServiceClientUnavailable
from app.services.prefect.client import make_prefect_client
from tests.utils.validation import create_validation_error


def test_make_prefect_client_raises_validation_error_on_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise create_validation_error()

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
