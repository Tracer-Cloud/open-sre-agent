"""Best-effort pleasant sound notifications for the interactive shell.

A short chime signals that a long turn finished or that the agent is waiting on
the user — useful when the terminal is not the focused window. Playback is
best-effort and never blocks the REPL or raises: on macOS it plays a system
sound, elsewhere it falls back to the terminal bell.
"""

from __future__ import annotations

import contextlib
import enum
import os
import platform
import re
import shutil
import subprocess
import sys

from config.constants.repl_sound import SOUND_NOTIFICATIONS_ENV

_TRUTHY = {"1", "true", "yes", "on"}

# TERM_PROGRAM value -> the macOS app whose window hosts this terminal, so the
# frontmost app can be matched against the one we run inside.
_TERM_PROGRAM_APP = {
    "Apple_Terminal": "Terminal",
    "iTerm.app": "iTerm2",
    "vscode": "Code",
    "WarpTerminal": "Warp",
    "Hyper": "Hyper",
    "ghostty": "Ghostty",
    "WezTerm": "WezTerm",
    "kitty": "kitty",
    "alacritty": "Alacritty",
    "Tabby": "Tabby",
}
# When TERM_PROGRAM is unset, treat any of these frontmost apps as "our terminal".
_KNOWN_TERMINAL_APPS = frozenset(_TERM_PROGRAM_APP.values()) | {"Electron"}
_LSAPPINFO_NAME_RE = re.compile(r'"LSDisplayName"="([^"]+)"')

# Distinct macOS system sounds so completion and a request for input are audibly
# different: a soft resolved tone vs. a lighter attention ping.
_MACOS_SOUNDS = {
    "turn_complete": "/System/Library/Sounds/Glass.aiff",
    "input_needed": "/System/Library/Sounds/Ping.aiff",
}


class NotifyEvent(enum.Enum):
    """What the chime is announcing."""

    TURN_COMPLETE = "turn_complete"
    INPUT_NEEDED = "input_needed"


def sound_enabled() -> bool:
    """True when the user opted into shell sound notifications."""
    return os.environ.get(SOUND_NOTIFICATIONS_ENV, "").strip().lower() in _TRUTHY


def _macos_frontmost_app() -> str | None:
    """Display name of the frontmost macOS app, or ``None`` (permission-free)."""
    with contextlib.suppress(Exception):
        front = subprocess.run(  # noqa: S603
            ["lsappinfo", "front"], capture_output=True, text=True, timeout=1
        ).stdout.strip()
        if not front:
            return None
        info = subprocess.run(  # noqa: S603
            ["lsappinfo", "info", "-only", "name", front],
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
        match = _LSAPPINFO_NAME_RE.search(info)
        if match:
            return match.group(1)
    return None


def terminal_is_focused() -> bool | None:
    """Whether this terminal's window is frontmost. ``None`` when undeterminable.

    Only resolvable on macOS (via the frontmost app). Off macOS — or if the
    lookup fails — returns ``None`` so the caller can degrade rather than guess.
    """
    if platform.system() != "Darwin":
        return None
    front = _macos_frontmost_app()
    if front is None:
        return None
    ours = _TERM_PROGRAM_APP.get(os.environ.get("TERM_PROGRAM", ""))
    if ours is not None:
        return front == ours
    return front in _KNOWN_TERMINAL_APPS


def play_notification(event: NotifyEvent) -> None:
    """Play a short chime for ``event`` — opt-in, best-effort, non-blocking.

    Stays silent while this terminal is the focused window (you are already
    looking); chimes when it is unfocused, or when focus cannot be determined.
    """
    if not sound_enabled():
        return
    # A notification must never disrupt the turn — swallow any focus-check or
    # playback error, and stay silent only when the terminal is definitely focused.
    with contextlib.suppress(Exception):
        if terminal_is_focused() is not True:
            _play(event)


def _play(event: NotifyEvent) -> None:
    if platform.system() == "Darwin":
        sound = _MACOS_SOUNDS.get(event.value, _MACOS_SOUNDS["turn_complete"])
        afplay = shutil.which("afplay")
        if afplay and os.path.exists(sound):
            # Detached and non-blocking: the REPL never waits on playback.
            subprocess.Popen(  # noqa: S603
                [afplay, sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    # Portable fallback: the terminal bell (the terminal decides how it sounds).
    sys.stdout.write("\a")
    sys.stdout.flush()


__all__ = ["NotifyEvent", "play_notification", "sound_enabled", "terminal_is_focused"]
