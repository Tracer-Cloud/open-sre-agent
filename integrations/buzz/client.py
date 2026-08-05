"""Buzz CLI client — subprocess wrapper around the ``buzz`` binary for message delivery.

``buzz-cli`` (crate ``buzz-cli``, binary name ``buzz``) is not published to any
package registry; it is built with ``cargo install --path crates/buzz-cli``
from https://github.com/block/buzz. This client therefore treats it as a soft
dependency the way ``integrations/helm/client.py`` treats ``helm`` — absence is
a value (:meth:`ProbeResult.missing`), never an unhandled exception.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from config.constants.buzz import BUZZ_PATH_ENV
from integrations.config_models import BuzzConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_CMD_TIMEOUT = 30.0
_PROBE_TIMEOUT = 15.0

# buzz-cli exit codes: 0 ok, 1 user error, 2 network/relay, 3 auth, 4 other,
# 5 write conflict (value superseded).
_EXIT_OK = 0
_EXIT_NETWORK_ERROR = 2
_EXIT_AUTH_ERROR = 3


def resolve_buzz_binary(buzz_path: str = "") -> str | None:
    """Resolve the ``buzz`` binary path.

    ``BUZZ_PATH`` env is checked first (matches ``HELM_PATH``/``RAILWAY_PATH``
    as the escape hatch for a non-``PATH`` install), then *buzz_path* — a
    literal file path or a bare name looked up on ``PATH``.
    """
    override = os.getenv(BUZZ_PATH_ENV, "").strip()
    raw = override or (buzz_path or "buzz").strip() or "buzz"
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return str(candidate)
    return shutil.which(raw)


def _parse_stderr_error(stderr: str) -> str:
    """Best-effort parse of buzz-cli's ``{"error": ..., "message": ...}`` stderr shape."""
    text = (stderr or "").strip()
    if not text:
        return "unknown error"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    return text


class BuzzClient:
    """Runs ``buzz-cli`` commands against a configured Buzz relay.

    The private key is injected via the ``BUZZ_PRIVATE_KEY`` environment
    variable only — never as an argv element — so it cannot leak through
    process listings or subprocess argument logging.
    """

    def __init__(self, config: BuzzConfig) -> None:
        self._config = config

    @property
    def is_configured(self) -> bool:
        return bool(self._config.private_key) and self._resolved_path() is not None

    def _resolved_path(self) -> str | None:
        return resolve_buzz_binary(self._config.buzz_path)

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["BUZZ_PRIVATE_KEY"] = self._config.private_key
        env["BUZZ_RELAY_URL"] = self._config.relay_url
        if self._config.auth_tag:
            env["BUZZ_AUTH_TAG"] = self._config.auth_tag
        return env

    def _run(
        self, args: list[str], *, timeout: float = _DEFAULT_CMD_TIMEOUT
    ) -> tuple[int, str, str]:
        path = self._resolved_path()
        if path is None:
            hint = (self._config.buzz_path or "buzz").strip() or "buzz"
            return 127, "", f"buzz executable not found ({hint!r})"
        cmd = [path, *args]
        logger.debug("buzz subprocess: %s subcommands", len(args))
        try:
            proc = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=self._env(),
            )
        except subprocess.TimeoutExpired:
            return 124, "", "buzz command timed out"
        except OSError as exc:
            return 1, "", f"buzz subprocess failed: {exc}"
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def probe_access(self) -> ProbeResult:
        if not self._config.private_key:
            return ProbeResult.missing(
                "Buzz is not configured: private_key (BUZZ_PRIVATE_KEY) is required."
            )
        if self._resolved_path() is None:
            hint = (self._config.buzz_path or "buzz").strip() or "buzz"
            return ProbeResult.missing(
                f"buzz CLI not found ({hint!r}). Install with `cargo install --path "
                "crates/buzz-cli` (from https://github.com/block/buzz) or set "
                "buzz_path/BUZZ_PATH to a binary."
            )
        code, out, err = self._run(["channels", "list"], timeout=_PROBE_TIMEOUT)
        if code == _EXIT_AUTH_ERROR:
            return ProbeResult.failed(f"Buzz authentication failed: {_parse_stderr_error(err)}")
        if code == _EXIT_NETWORK_ERROR:
            return ProbeResult.failed(
                f"Buzz relay unreachable ({self._config.relay_url}): {_parse_stderr_error(err)}"
            )
        if code != _EXIT_OK:
            return ProbeResult.failed(
                f"buzz channels list failed (exit {code}): {_parse_stderr_error(err)}"
            )
        channel_count = ""
        try:
            parsed = json.loads(out or "[]")
            if isinstance(parsed, list):
                channel_count = f" ({len(parsed)} channel(s) visible)"
        except json.JSONDecodeError:
            # Channel count is a display enrichment, not a correctness signal —
            # the exit code already confirmed success, so skip it silently.
            logger.debug("buzz channels list returned non-JSON output; omitting channel count")
        return ProbeResult.passed(
            f"Connected to Buzz relay at {self._config.relay_url}{channel_count}."
        )

    def send_message(
        self,
        *,
        channel: str,
        content: str,
        reply_to: str = "",
    ) -> dict[str, Any]:
        """Send a message via ``buzz messages send``.

        Returns ``{"success": bool, "error": str, "event_id": str}``.
        """
        chan = channel.strip()
        if not chan:
            return {"success": False, "error": "channel is required", "event_id": ""}
        args = ["messages", "send", "--channel", chan, "--content", content]
        if reply_to.strip():
            args.extend(["--reply-to", reply_to.strip()])
        code, out, err = self._run(args)
        if code != _EXIT_OK:
            return {
                "success": False,
                "error": _parse_stderr_error(err) or (out or "").strip() or f"exit {code}",
                "event_id": "",
            }
        try:
            payload = json.loads(out or "{}")
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": "invalid JSON from buzz messages send",
                "event_id": "",
            }
        if not isinstance(payload, dict):
            return {
                "success": False,
                "error": "unexpected buzz messages send response shape",
                "event_id": "",
            }
        event_id = str(payload.get("event_id") or "")
        if not bool(payload.get("accepted", True)):
            return {
                "success": False,
                "error": str(payload.get("message") or "message not accepted"),
                "event_id": event_id,
            }
        return {"success": True, "error": "", "event_id": event_id}


__all__ = ["BuzzClient", "resolve_buzz_binary"]
