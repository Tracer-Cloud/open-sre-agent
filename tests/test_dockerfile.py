"""Tests for the production Dockerfile.

These tests validate that the Dockerfile at the repo root is correctly
structured for production deployment.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def dockerfile_path() -> Path:
    """Return the path to the root Dockerfile."""
    return Path(__file__).parent.parent / "Dockerfile"


def test_dockerfile_exists(dockerfile_path: Path) -> None:
    """The Dockerfile must exist at the repo root."""
    assert dockerfile_path.exists(), "Dockerfile not found at repo root"
    assert dockerfile_path.is_file(), "Dockerfile is not a file"


def test_dockerfile_uses_python_slim_base(dockerfile_path: Path) -> None:
    """The Dockerfile must use a supported Python base image."""
    content = dockerfile_path.read_text()
    assert "FROM python:3.12-slim" in content, "Should use python:3.12-slim base image"


def test_dockerfile_installs_dependencies(dockerfile_path: Path) -> None:
    """The Dockerfile must install the package without editable mode."""
    content = dockerfile_path.read_text()
    assert "pip install" in content, "Should install the package"
    assert "pip install -e" not in content, "Should avoid editable installs in production"


def test_dockerfile_runs_uvicorn_webapp(dockerfile_path: Path) -> None:
    """The Dockerfile must start the FastAPI app with uvicorn."""
    content = dockerfile_path.read_text()
    assert "uvicorn app.webapp:app" in content, "Should start the FastAPI application"


def test_dockerfile_exposes_port_8000(dockerfile_path: Path) -> None:
    """The Dockerfile must expose the HTTP port."""
    content = dockerfile_path.read_text()
    assert "EXPOSE 8000" in content, "Should expose port 8000"


def test_dockerfile_has_healthcheck(dockerfile_path: Path) -> None:
    """The Dockerfile must have a HEALTHCHECK instruction."""
    content = dockerfile_path.read_text()
    assert "HEALTHCHECK" in content, "Should have HEALTHCHECK instruction"


def test_dockerfile_healthcheck_hits_health_route(dockerfile_path: Path) -> None:
    """The health check should verify the /health endpoint is accessible."""
    content = dockerfile_path.read_text()
    assert ":8000/health" in content, "Should check /health endpoint for health"


def test_dockerfile_copies_app_code(dockerfile_path: Path) -> None:
    """The Dockerfile must copy the application code."""
    content = dockerfile_path.read_text()
    assert "COPY . /app" in content, "Should copy the repository into the image"


def test_dockerfile_has_cmd_to_start_server(dockerfile_path: Path) -> None:
    """The Dockerfile must define a CMD that starts the ASGI server."""
    content = dockerfile_path.read_text()
    assert "CMD" in content, "Should define a container start command"
