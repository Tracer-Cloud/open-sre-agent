from __future__ import annotations

import os
import re
import subprocess
import sys

from config.version import get_opensre_version

_MAIN_BUILD_RELEASE_API = (
    "https://api.github.com/repos/Tracer-Cloud/opensre/releases/tags/main-build"
)
_INSTALL_SCRIPT = "https://install.opensre.com"
_INSTALL_SCRIPT_PS1 = "https://install.opensre.com"
_MAIN_BUILD_RELEASE_URL = "https://github.com/Tracer-Cloud/opensre/releases/tag/main-build"
_VERSION_FROM_RELEASE_BODY = re.compile(r"Version:\s*`([^`\n]+)`", re.IGNORECASE)
_MAIN_BUILD_SHA_SUFFIX = re.compile(r"\+main\.([0-9a-f]+)$", re.IGNORECASE)


def _main_build_release_api_url() -> str:
    return os.getenv("OPENSRE_RELEASES_API_URL", _MAIN_BUILD_RELEASE_API)


def _extract_main_build_version(release_body: str) -> str:
    match = _VERSION_FROM_RELEASE_BODY.search(release_body)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_main_build_sha(version: str) -> str | None:
    match = _MAIN_BUILD_SHA_SUFFIX.search(version.strip())
    if not match:
        return None
    return match.group(1).lower()


def _fetch_latest_version() -> str:
    import httpx

    try:
        resp = httpx.get(_main_build_release_api_url(), timeout=10, follow_redirects=True)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise RuntimeError("request timed out") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise RuntimeError("GitHub API rate limit exceeded, try again later") from exc
        raise RuntimeError(f"GitHub API returned HTTP {exc.response.status_code}") from exc
    except httpx.ConnectError as exc:
        raise RuntimeError(
            "could not connect to GitHub — check your network or HTTPS_PROXY settings"
        ) from exc

    body = resp.json().get("body") or ""
    return _extract_main_build_version(body)


def _is_update_available(current: str, latest: str) -> bool:
    current_main_sha = _extract_main_build_sha(current)
    latest_main_sha = _extract_main_build_sha(latest)
    if current_main_sha is not None and latest_main_sha is not None:
        # Same-day main rebuilds share a calendar prefix; compare commit SHAs
        # instead of PEP 440 local segments (hex SHAs are not ordered chronologically).
        return current_main_sha != latest_main_sha

    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return current != latest
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return current != latest


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_editable_install() -> bool:
    import importlib.metadata
    import json

    try:
        dist = importlib.metadata.distribution("opensre")
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            info = json.loads(direct_url_text)
            return bool(info.get("dir_info", {}).get("editable", False))
    except (importlib.metadata.PackageNotFoundError, json.JSONDecodeError, OSError):
        return False
    return False


def development_install_doctor_version_detail(current: str) -> str | None:
    """If this process looks like a local checkout, return the doctor line (skip release compare).

    Editable installs (`pip install -e` / ``uv sync`` on a git checkout) and ``uv run``
    children set signals we use so ``opensre doctor`` does not warn vs GitHub main builds.
    """
    labels: list[str] = []
    if _is_editable_install():
        labels.append("editable install")
    # uv sets this on the Python process when invoked via `uv run …`.
    if os.environ.get("UV_RUN_RECURSION_DEPTH") is not None:
        labels.append("uv run")
    if not labels:
        return None
    ctx = " + ".join(labels)
    return f"{current} ({ctx}; skipped comparing to latest main build)"


def _upgrade_via_install_script() -> int:
    """Download and run the official install script on the rolling main channel."""
    if _is_windows():
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$env:OPENSRE_INSTALL_CHANNEL='main'; "
                    f"Remove-Item Env:OPENSRE_VERSION -ErrorAction SilentlyContinue; "
                    f"irm {_INSTALL_SCRIPT_PS1} | iex"
                ),
            ],
            check=False,
        )
    else:
        result = subprocess.run(
            ["bash", "-c", f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"],
            check=False,
        )
    return result.returncode


def run_update(*, check_only: bool = False, yes: bool = False) -> int:
    # To skip this check in CI or automated environments, set OPENSRE_NO_UPDATE_CHECK=1.
    current = get_opensre_version()

    try:
        latest = _fetch_latest_version()
    except Exception as exc:
        print(f"  error: could not fetch latest version: {exc}", file=sys.stderr)
        return 1

    if not latest:
        print(
            "  error: could not determine latest main build version from release data.",
            file=sys.stderr,
        )
        return 1

    if not _is_update_available(current, latest):
        print(f"  opensre {current} is already up to date.")
        return 0

    print(f"  current: {current}")
    print(f"  latest:  {latest}")
    print("  main build: " + _MAIN_BUILD_RELEASE_URL)

    if check_only:
        return 1

    if _is_editable_install():
        print(
            "  warning: this is an editable install — upgrading will replace it with a main build."
        )

    if not yes:
        try:
            import questionary

            confirmed = questionary.confirm(f"  Update to main build {latest}?", default=True).ask()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1
        if not confirmed:
            print("  Cancelled.")
            return 0

    rc = _upgrade_via_install_script()
    if rc == 0:
        print(f"  updated: {current} -> {latest}")
        print("  main build release: " + _MAIN_BUILD_RELEASE_URL)
    else:
        print(f"  install script failed (exit {rc}).", file=sys.stderr)
        if _is_windows():
            hint = f"$env:OPENSRE_INSTALL_CHANNEL='main'; irm {_INSTALL_SCRIPT_PS1} | iex"
        else:
            hint = f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"
        print(f"  to retry manually, run:\n    {hint}", file=sys.stderr)
    return rc
