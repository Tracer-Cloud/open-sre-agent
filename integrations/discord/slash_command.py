"""Registering OpenSRE's ``/investigate`` slash command with a Discord app.

Discord commands are registered by pushing them to the app over the REST API,
which can only happen once the bot token and application id exist — i.e. after
the integration is saved. Setup wires this as the spec's ``finalize`` step, so
it returns a note for the success detail rather than printing: whether the
command registered is information about a setup that already succeeded, not a
gate on it.
"""

from __future__ import annotations

from config.constants.discord import DISCORD_API_BASE

_COMMANDS_URL = DISCORD_API_BASE + "/applications/{application_id}/commands"
_INVESTIGATE_COMMAND = {
    "name": "investigate",
    "description": "Trigger an OpenSRE investigation",
    "options": [
        {
            "name": "alert",
            "description": "Alert JSON or description",
            "type": 3,
            "required": True,
        }
    ],
}


def register_investigate_command(application_id: str, bot_token: str) -> str:
    """Register the ``/investigate`` command, returning a note on the outcome.

    Best-effort by contract: the integration is already configured, so a
    failure here is reported (the user can re-run to retry) but never unwinds
    the save. Never raises.
    """
    import httpx

    url = _COMMANDS_URL.format(application_id=application_id)
    try:
        resp = httpx.put(
            url,
            json=[_INVESTIGATE_COMMAND],
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        return f"/investigate slash command not registered ({type(exc).__name__}); re-run to retry."

    if resp.is_success:
        return "/investigate slash command registered."
    return f"/investigate slash command registration failed (HTTP {resp.status_code}); re-run to retry."


__all__ = ["register_investigate_command"]
