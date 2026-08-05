"""Live LLM investigation for the quickstart fixture path.

Not in GitHub Actions (``ci.yml`` e2e-general ignores this path) and not in
default pytest (``norecursedirs = tests/e2e``). Opt-in only:

    OPENSRE_LIVE_QUICKSTART=1 uv run pytest tests/e2e/quickstart/ -q
    ./trace smoke --suite all --only agent.investigate.quickstart_k8s

Offline quickstart coverage stays in CI via ``tests/cli/test_quickstart.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.constants.paths import REPO_ROOT

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("OPENSRE_LIVE_QUICKSTART") != "1",
        reason="Set OPENSRE_LIVE_QUICKSTART=1 to run the live quickstart investigation",
    ),
]

QUICKSTART_ALERT = (
    REPO_ROOT / "tests" / "e2e" / "kubernetes" / "fixtures" / "datadog_k8s_alert.json"
)
QUICKSTART_ALERT_DOC_PATH = "tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json"


def _opensre_cmd() -> list[str]:
    """Prefer the editable module entry — never a Homebrew/PyInstaller binary on PATH."""
    return [sys.executable, "-m", "surfaces.cli.app"]


def test_live_quickstart_investigate_completes(tmp_path: Path) -> None:
    """``opensre investigate -i <quickstart fixture>`` finishes an RCA with live LLM."""
    assert QUICKSTART_ALERT.is_file()
    out_json = tmp_path / "investigation.json"

    env = os.environ.copy()
    # Keep the checkout's venv python first; drop brew opensre shadows.
    venv_bin = str(REPO_ROOT / ".venv" / "bin")
    path_parts = [venv_bin] + [
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if p and p != "/opt/homebrew/bin" and "Cellar/opensre" not in p
    ]
    env["PATH"] = os.pathsep.join(path_parts)

    cmd = [
        *_opensre_cmd(),
        "investigate",
        "-i",
        QUICKSTART_ALERT_DOC_PATH,
        "-o",
        str(out_json),
    ]
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert out_json.is_file(), f"expected -o JSON at {out_json}\n{combined}"

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    # Investigate JSON always carries a report / root_cause style payload.
    text_blob = json.dumps(payload).lower()
    assert any(
        key in payload for key in ("root_cause", "report", "problem_md", "slack_message")
    ) or any(
        token in text_blob for token in ("root_cause", "hypothesis", "investigation", "evidence")
    ), f"unexpected investigate payload keys: {list(payload)[:20]}"
