"""Sandbox runner static constants."""

from __future__ import annotations

# Env keys the sandbox child receives. Keys in SANDBOX_BASE_ENV_KEYS are
# forwarded from the host when set; keys in SANDBOXED_TEMP_ENV_KEYS are not
# forwarded -- they are rewritten to point at the sandbox temp root so that
# child-side temp-file APIs (tempfile.mkstemp and friends read TEMP/TMP on
# Windows and TMPDIR on POSIX) cannot write outside the sandbox root, because
# the injected guard only intercepts builtins.open.
SANDBOX_BASE_ENV_KEYS: tuple[str, ...] = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONPATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "TMPDIR",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)

SANDBOXED_TEMP_ENV_KEYS: tuple[str, ...] = (
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
)
