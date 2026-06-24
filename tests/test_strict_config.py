from __future__ import annotations

import pytest

from app.strict_config import StrictConfigModel


class ExampleConfig(StrictConfigModel):
    name: str
    optional_value: str | None = None


def test_strict_config_supports_dict_style_field_access() -> None:
    config = ExampleConfig(name="service")

    assert config["name"] == "service"
    assert config["optional_value"] is None
    assert config.get("missing", "fallback") == "fallback"


def test_strict_config_dict_style_unknown_key_raises_key_error() -> None:
    config = ExampleConfig(name="service")

    with pytest.raises(KeyError):
        _ = config["missing"]
