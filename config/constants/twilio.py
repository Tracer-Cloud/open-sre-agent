"""Twilio environment variable names.

Account credentials are shared across every channel on one Twilio account —
``TWILIO_ACCOUNT_SID``/``TWILIO_AUTH_TOKEN`` are not WhatsApp- or SMS-specific.
Running setup for one channel mirrors these two into the same env vars a
different channel's setup would also read; that is the intended behavior, not
a collision, since both draw on the same account.
"""

from __future__ import annotations

TWILIO_ACCOUNT_SID_ENV = "TWILIO_ACCOUNT_SID"
TWILIO_AUTH_TOKEN_ENV = "TWILIO_AUTH_TOKEN"
TWILIO_WHATSAPP_FROM_ENV = "TWILIO_WHATSAPP_FROM"
WHATSAPP_DEFAULT_TO_ENV = "WHATSAPP_DEFAULT_TO"

__all__ = [
    "TWILIO_ACCOUNT_SID_ENV",
    "TWILIO_AUTH_TOKEN_ENV",
    "TWILIO_WHATSAPP_FROM_ENV",
    "WHATSAPP_DEFAULT_TO_ENV",
]
