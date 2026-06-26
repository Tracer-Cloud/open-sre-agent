from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from integrations.llm_cli.probe_utils import run_version_probe


@patch("integrations.llm_cli.probe_utils.subprocess.run")
def test_run_version_probe_success(mock_run: MagicMock) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=["/bin/tool", "--version"],
        returncode=0,
        stdout="1.2.3\n",
        stderr="",
    )

    output, detail = run_version_probe("/bin/tool", timeout_sec=3.0)

    assert output == "1.2.3\n"
    assert detail is None


@patch("integrations.llm_cli.probe_utils.subprocess.run")
def test_run_version_probe_nonzero(mock_run: MagicMock) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=["/bin/tool", "--version"],
        returncode=2,
        stdout="",
        stderr="broken",
    )

    output, detail = run_version_probe("/bin/tool", timeout_sec=3.0)

    assert output is None
    assert detail == "`/bin/tool --version` failed: broken"


@patch("integrations.llm_cli.probe_utils.subprocess.run")
def test_run_version_probe_timeout(mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["/bin/tool", "--version"], timeout=3.0)

    output, detail = run_version_probe("/bin/tool", timeout_sec=3.0)

    assert output is None
    assert detail is not None
    assert detail.startswith("Could not run `/bin/tool --version`:")


@patch("integrations.llm_cli.probe_utils.subprocess.run")
def test_run_version_probe_oserror(mock_run: MagicMock) -> None:
    mock_run.side_effect = OSError("no exec")

    output, detail = run_version_probe("/bin/tool", timeout_sec=3.0)

    assert output is None
    assert detail == "Could not run `/bin/tool --version`: no exec"
