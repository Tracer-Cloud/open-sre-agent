"""Gateway runtime constants."""

ATTACHMENT_MAX_FILE_CHARS = 40_000
ATTACHMENT_MAX_TOTAL_CHARS = 120_000
CREDITS_DENIED_MESSAGE = "Out of credits — top up in the OpenSRE console."
#: Overall SIGTERM budget for web + chat workers. Sequential stop steps share it.
DEFAULT_STOP_TIMEOUT_SECONDS = 8.0
#: Web is a thread join, not a network drain, so it keeps a smaller slice.
WEB_STOP_TIMEOUT_SECONDS = 5.0
#: Reload watcher only polls a flag; cap the join so chat workers keep the rest.
SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_CONVERSATION_LOCKS = 1024
NEW_SESSION_MESSAGE = "Started a new session."
#: Inbound-decision reply sentinel: rotate the session instead of replying.
ROTATE_SESSION = "__ROTATE_SESSION__"
NO_ACTIVE_TURN_MESSAGE = "Nothing running to stop."
TURN_ERROR_MESSAGE = "Something went wrong on that request."
TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."
UNAUTHORIZED_MESSAGE = "You're not authorized to use this bot. Ask an admin to add you."
USER_STOP_MESSAGE = "Stopped."

#: Postgres DSN for the gateway's shared repositories; unset means process-local storage.
DATABASE_URL_ENV = "DATABASE_URL"

#: Opt-in switch: run the background investigation worker in this process.
INVESTIGATION_WORKER_ENABLED_ENV = "OPENSRE_INVESTIGATION_WORKER"

#: Number of concurrent investigation worker threads; unset or invalid means one.
INVESTIGATION_WORKERS_ENV = "OPENSRE_INVESTIGATION_WORKERS"

__all__ = [
    "DATABASE_URL_ENV",
    "INVESTIGATION_WORKER_ENABLED_ENV",
    "INVESTIGATION_WORKERS_ENV",
    "ATTACHMENT_MAX_FILE_CHARS",
    "ATTACHMENT_MAX_TOTAL_CHARS",
    "CREDITS_DENIED_MESSAGE",
    "DEFAULT_MAX_CONVERSATION_LOCKS",
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "NEW_SESSION_MESSAGE",
    "ROTATE_SESSION",
    "SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS",
    "NO_ACTIVE_TURN_MESSAGE",
    "TURN_ERROR_MESSAGE",
    "TURN_TIMEOUT_MESSAGE",
    "UNAUTHORIZED_MESSAGE",
    "USER_STOP_MESSAGE",
    "WEB_STOP_TIMEOUT_SECONDS",
]
