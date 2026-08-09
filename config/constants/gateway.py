"""Gateway runtime constants."""

DEFAULT_MAX_CONVERSATION_LOCKS = 1024
TURN_ERROR_MESSAGE = "Something went wrong on that request."
TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."
USER_STOP_MESSAGE = "Stopped."

__all__ = [
    "DEFAULT_MAX_CONVERSATION_LOCKS",
    "TURN_ERROR_MESSAGE",
    "TURN_TIMEOUT_MESSAGE",
    "USER_STOP_MESSAGE",
]
