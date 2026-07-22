"""What WhatsApp (Twilio) needs before it is considered configured.

``account_sid``/``auth_token`` mirror the shared Twilio account credentials —
see ``config/constants/twilio.py``. Setting up WhatsApp and (once migrated)
Twilio SMS write the same two env vars, by design: they are one account.
"""

from __future__ import annotations

from config.constants.twilio import (
    TWILIO_ACCOUNT_SID_ENV,
    TWILIO_AUTH_TOKEN_ENV,
    TWILIO_WHATSAPP_FROM_ENV,
    WHATSAPP_DEFAULT_TO_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.whatsapp.verifier import verify_whatsapp

ACCOUNT_SID_FIELD = "account_sid"
AUTH_TOKEN_FIELD = "auth_token"
FROM_NUMBER_FIELD = "from_number"
DEFAULT_TO_FIELD = "default_to"

WHATSAPP_SETUP = IntegrationSetupSpec(
    service="whatsapp",
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
            label="Twilio WhatsApp From number",
            prompt="Twilio WhatsApp From number (e.g. whatsapp:+14155238886)",
            env_var=TWILIO_WHATSAPP_FROM_ENV,
        ),
        SetupField(
            name=DEFAULT_TO_FIELD,
            label="Default recipient phone number",
            prompt="Default recipient phone number (optional, e.g. +1234567890)",
            env_var=WHATSAPP_DEFAULT_TO_ENV,
            required=False,
        ),
    ),
    verify=verify_whatsapp,
)

__all__ = [
    "ACCOUNT_SID_FIELD",
    "AUTH_TOKEN_FIELD",
    "DEFAULT_TO_FIELD",
    "FROM_NUMBER_FIELD",
    "WHATSAPP_SETUP",
]
