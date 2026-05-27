from __future__ import annotations

import json
import subprocess

import pytest

# Bound every docker subprocess so a wedged daemon, slow pull, or blocking
# module-level import in the container can't hang the test run indefinitely.
# `docker image inspect` is local metadata and finishes in milliseconds, so a
# tight cap is fine; `docker run` does a one-shot container start, so it gets
# a more generous cap to cover cold image-layer extraction on first use.
_INSPECT_TIMEOUT_SEC = 30
_RUN_TIMEOUT_SEC = 60


def _inspect_image(image_tag: str) -> dict:
    """Return the parsed `docker image inspect` JSON object for `image_tag`."""
    result = subprocess.run(
        ["docker", "image", "inspect", image_tag],
        check=True,
        capture_output=True,
        text=True,
        timeout=_INSPECT_TIMEOUT_SEC,
    )
    payload = json.loads(result.stdout)
    assert payload, f"docker image inspect returned no entries for {image_tag}"
    return payload[0]


@pytest.fixture(scope="session")
def deploy_image_config(deploy_image_tag: str) -> dict:
    """Run `docker image inspect` once per session and return the `Config` block.

    Multiple tests assert on different fields of the same `Config` payload
    (`Cmd`, `ExposedPorts`, `Healthcheck`). Caching the parsed result here
    avoids spawning a separate inspect subprocess per test.
    """
    inspected = _inspect_image(deploy_image_tag)
    assert "Config" in inspected, (
        f"docker image inspect did not return a Config block for {deploy_image_tag!r}"
    )
    return inspected["Config"]


def test_dockerfile_build_succeeds(deploy_image_tag: str) -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", deploy_image_tag],
        check=False,
        capture_output=True,
        text=True,
        timeout=_INSPECT_TIMEOUT_SEC,
    )
    assert result.returncode == 0, result.stderr


def test_image_cmd_runs_uvicorn(deploy_image_config: dict) -> None:
    """The image's CMD must launch uvicorn against `app.webapp:app`."""
    cmd = deploy_image_config.get("Cmd") or []
    joined = " ".join(cmd)

    assert "uvicorn" in joined, f"expected uvicorn in image CMD, got: {cmd!r}"
    assert "app.webapp:app" in joined, f"expected app.webapp:app in image CMD, got: {cmd!r}"


def test_image_exposes_port_8000(deploy_image_config: dict) -> None:
    """The image must expose 8000/tcp so hosts can publish the FastAPI port."""
    exposed = deploy_image_config.get("ExposedPorts") or {}

    assert "8000/tcp" in exposed, f"expected 8000/tcp in ExposedPorts, got: {sorted(exposed)!r}"


def test_image_declares_healthcheck(deploy_image_config: dict) -> None:
    """The image must declare a HEALTHCHECK that probes the /health endpoint."""
    healthcheck = deploy_image_config.get("Healthcheck") or {}
    test_cmd = healthcheck.get("Test") or []
    joined = " ".join(test_cmd)

    assert test_cmd, "expected the image to declare a HEALTHCHECK directive"
    assert "/health" in joined, f"expected /health probe in HEALTHCHECK, got: {test_cmd!r}"


def test_image_does_not_contain_dotenv(deploy_image_tag: str) -> None:
    """The build context must not leak a `.env` file into the image."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            deploy_image_tag,
            "-c",
            "test ! -f /app/.env",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=_RUN_TIMEOUT_SEC,
    )
    assert result.returncode == 0, (
        "image contains /app/.env — secrets must not be baked into the build context"
    )


def test_image_can_import_webapp(deploy_image_tag: str) -> None:
    """A one-shot container run must be able to import the FastAPI app module.

    This validates that the install succeeded and every transitive dependency
    resolves at runtime, without the flakiness of starting uvicorn and polling
    a port.
    """
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            deploy_image_tag,
            "-c",
            "from app.webapp import app; print(type(app).__name__)",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=_RUN_TIMEOUT_SEC,
    )
    assert result.returncode == 0, (
        f"importing app.webapp inside the image failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FastAPI" in result.stdout, (
        f"expected FastAPI app in container stdout, got: {result.stdout!r}"
    )
