from __future__ import annotations

from types import SimpleNamespace

from gateway.transports.discord.worker import (
    application_command_name,
    application_command_option,
    investigate_slash_text,
)


def test_application_command_name_from_rest_payload_when_command_is_none() -> None:
    interaction = SimpleNamespace(
        command=None,
        data={"name": "investigate", "options": [{"name": "alert", "value": "cpu high"}]},
        namespace=None,
    )
    assert application_command_name(interaction) == "investigate"  # type: ignore[arg-type]


def test_application_command_option_from_rest_payload() -> None:
    interaction = SimpleNamespace(
        command=None,
        data={"name": "investigate", "options": [{"name": "alert", "value": "cpu high"}]},
        namespace=None,
    )
    assert application_command_option(interaction, "alert") == "cpu high"  # type: ignore[arg-type]


def test_investigate_slash_text_template() -> None:
    assert investigate_slash_text("generic") == "/investigate generic"
    assert investigate_slash_text("") == "/investigate generic"


def test_investigate_slash_text_freeform() -> None:
    assert investigate_slash_text("checkout errors") == "/investigate alert:checkout errors"
