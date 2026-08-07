"""What Twilio SMS needs before it is considered configured.

Account SID and auth token are required. The SMS sender is either a
``from_number`` or a ``messaging_service_sid`` — both fields are optional here,
and :func:`integrations.twilio.verifier.verify_twilio` enforces that one of
them is present (same rule health checks use).

Credentials are collected flat so every field can mirror an env var; the
classifier and verifier reshape the SMS fields into the nested ``sms`` dict
the config model expects.
"""

from __future__ import annotations

from typing import Any

from config.constants.twilio import (
    TWILIO_ACCOUNT_SID_ENV,
    TWILIO_AUTH_TOKEN_ENV,
    TWILIO_SMS_DEFAULT_TO_ENV,
    TWILIO_SMS_FROM_ENV,
    TWILIO_SMS_MESSAGING_SERVICE_SID_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.twilio.verifier import verify_twilio

ACCOUNT_SID_FIELD = "account_sid"
AUTH_TOKEN_FIELD = "auth_token"
FROM_NUMBER_FIELD = "from_number"
MESSAGING_SERVICE_SID_FIELD = "messaging_service_sid"
DEFAULT_TO_FIELD = "default_to"


def shape_twilio_credentials(config: dict[str, Any]) -> dict[str, Any]:
    """Return *config* with a nested ``sms`` block when fields were collected flat."""
    if isinstance(config.get("sms"), dict) and config["sms"]:
        return config
    from_number = str(config.get(FROM_NUMBER_FIELD) or "").strip()
    messaging_service_sid = str(config.get(MESSAGING_SERVICE_SID_FIELD) or "").strip()
    default_to = str(config.get(DEFAULT_TO_FIELD) or "").strip() or None
    if not (from_number or messaging_service_sid):
        return config
    shaped = dict(config)
    shaped["sms"] = {
        "enabled": True,
        "from_number": from_number,
        "messaging_service_sid": messaging_service_sid,
        "default_to": default_to,
    }
    return shaped


def _verify_twilio_setup(source: str, config: dict[str, Any]) -> dict[str, str]:
    return verify_twilio(source, shape_twilio_credentials(config))


TWILIO_SETUP = IntegrationSetupSpec(
    service="twilio",
    fields=(
        SetupField(
            name=ACCOUNT_SID_FIELD,
            label="Twilio Account SID",
            prompt="Twilio Account SID (starts with AC...)",
            env_var=TWILIO_ACCOUNT_SID_ENV,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Twilio Auth Token",
            env_var=TWILIO_AUTH_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=FROM_NUMBER_FIELD,
            label="Twilio SMS From number",
            prompt=(
                "Twilio SMS From number (E.164, e.g. +14155551234; "
                "leave blank to use a Messaging Service SID)"
            ),
            env_var=TWILIO_SMS_FROM_ENV,
            required=False,
        ),
        SetupField(
            name=MESSAGING_SERVICE_SID_FIELD,
            label="Twilio Messaging Service SID",
            prompt="Twilio Messaging Service SID (starts with MG...)",
            env_var=TWILIO_SMS_MESSAGING_SERVICE_SID_ENV,
            required=False,
        ),
        SetupField(
            name=DEFAULT_TO_FIELD,
            label="Default SMS recipient",
            prompt="Default SMS recipient (optional, E.164)",
            env_var=TWILIO_SMS_DEFAULT_TO_ENV,
            required=False,
        ),
    ),
    verify=_verify_twilio_setup,
)

__all__ = [
    "ACCOUNT_SID_FIELD",
    "AUTH_TOKEN_FIELD",
    "DEFAULT_TO_FIELD",
    "FROM_NUMBER_FIELD",
    "MESSAGING_SERVICE_SID_FIELD",
    "TWILIO_SETUP",
    "shape_twilio_credentials",
]
