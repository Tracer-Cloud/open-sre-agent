"""Backend-aware availability check for Hermes tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations

from pathlib import Path

from integrations.hermes.config import default_hermes_log_path


def _is_readable_log_file(path: Path) -> bool:
    """Return whether ``path`` is a readable regular file."""
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def hermes_available_or_backend(sources: dict[str, dict]) -> bool:
    """Require a readable configured log or an injected fixture backend."""
    hermes = sources.get("hermes", {})
    if hermes.get("_backend") is not None:
        return True
    if not hermes.get("connection_verified"):
        return False

    configured = str(hermes.get("log_path") or "").strip()
    log_path = Path(configured).expanduser() if configured else default_hermes_log_path()
    return _is_readable_log_file(log_path)
