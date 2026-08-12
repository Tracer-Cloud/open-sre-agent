"""Gateway runtime constants."""

ATTACHMENT_MAX_FILE_CHARS = 40_000
ATTACHMENT_MAX_TOTAL_CHARS = 120_000
CREDITS_DENIED_MESSAGE = "Out of credits — top up in the OpenSRE console."
DEFAULT_MAX_CONVERSATION_LOCKS = 1024
NEW_SESSION_MESSAGE = "Started a new session."
NO_ACTIVE_TURN_MESSAGE = "Nothing running to stop."
TURN_ERROR_MESSAGE = "Something went wrong on that request."
TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."
UNAUTHORIZED_MESSAGE = "You're not authorized to use this bot. Ask an admin to add you."
USER_STOP_MESSAGE = "Stopped."

__all__ = [
    "ATTACHMENT_MAX_FILE_CHARS",
    "ATTACHMENT_MAX_TOTAL_CHARS",
    "CREDITS_DENIED_MESSAGE",
    "DEFAULT_MAX_CONVERSATION_LOCKS",
    "NEW_SESSION_MESSAGE",
    "NO_ACTIVE_TURN_MESSAGE",
    "TURN_ERROR_MESSAGE",
    "TURN_TIMEOUT_MESSAGE",
    "UNAUTHORIZED_MESSAGE",
    "USER_STOP_MESSAGE",
]
