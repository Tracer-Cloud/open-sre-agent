"""Contracts for the PowerShell installer progress helpers."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

INSTALL_PS1 = Path(__file__).parents[2] / "install.ps1"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def test_install_ps1_defines_branded_progress_helpers() -> None:
    source = INSTALL_PS1.read_text()

    for helper in (
        "function Write-OpenSreHeader",
        "function Test-OpenSreInteractiveHost",
        "function Get-OpenSreConsoleWidth",
        "function Limit-OpenSreText",
        "function Get-OpenSreFriendlyProgressLabel",
        "function Get-OpenSreProgressFrame",
        "function New-OpenSreProgressBar",
        "function Invoke-OpenSreStep",
        "function Invoke-OpenSreDownloadFileWithProgress",
    ):
        assert helper in source

    assert "OPENSRE_INSTALL_VERBOSE" in source
    assert '$ProgressPreference = "SilentlyContinue"' in source
    assert "$ProgressPreference = $previousProgressPreference" in source
    assert "Clear-Host" not in source
    assert "preparing installer" not in source


def test_install_ps1_avoids_ps7_only_syntax_and_write_progress() -> None:
    source = INSTALL_PS1.read_text()

    forbidden_snippets = (
        "$PSStyle",
        "??",
        "Join-String",
        "-SkipHttpErrorCheck",
        "Write-Progress",
    )
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_install_ps1_preserves_retry_contract_source() -> None:
    source = INSTALL_PS1.read_text()

    assert 'Write-Warning "Attempt $attempt to $Description failed' in source
    assert "after $attempt attempts" in source
    assert "$statusCode -ge 400 -and $statusCode -lt 500" in source


def test_install_ps1_defaults_to_main_build_channel() -> None:
    source = INSTALL_PS1.read_text()

    assert 'else { "main" }' in source
    assert 'else { "main-build" }' in source
    assert "releases/tags/$mainReleaseTag" in source
    assert "$script:OpenSreChannelExplicit" in source
    assert '$resolvedChannel = "release"' in source
    assert "releases/tags/nightly" not in source


def test_install_ps1_contains_auto_onboarding_launch_hook() -> None:
    source = INSTALL_PS1.read_text()

    assert "function Test-OpenSreAutoLaunchEnabled" in source
    assert "function Start-OpenSreOnboardingAfterInstall" in source
    assert "OPENSRE_AUTO_LAUNCH" in source
    assert "& $BinaryPath onboard" in source
    assert "Start-OpenSreOnboardingAfterInstall -BinaryPath $installedBinaryPath" in source
    # A redirected/piped host must be treated as non-interactive so the
    # full-screen prompt is not launched into a terminal it cannot control
    # (issue #3273).
    assert "[System.Console]::IsInputRedirected" in source


def test_install_ps1_keeps_download_urls_verbose_only() -> None:
    source = INSTALL_PS1.read_text()

    assert 'Write-OpenSreDetail -Message "Download URL: $Uri"' in source
    assert 'Write-OpenSreDetail -Message "Destination: $OutFile"' in source
    assert "-Detail $downloadUrl" not in source
    assert "-Detail $checksumUrl" not in source


def test_install_ps1_uses_bounded_short_progress_labels() -> None:
    source = INSTALL_PS1.read_text()

    assert "Get-OpenSreConsoleWidth" in source
    assert "Limit-OpenSreText -Text (Get-OpenSreFriendlyProgressLabel -Label $Label)" in source
    assert "Installing OpenSRE" in source
    assert "downloading archive" in source
    assert "verifying checksum" in source
    assert '" " * 100' not in source
    assert '[System.Console]::Write("`r{0}`r{1}"' in source


def test_install_ps1_dot_sources_when_powershell_available() -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not installed in this environment.")

    script = textwrap.dedent(
        f"""
        . '{INSTALL_PS1}' -SkipMain
        Write-OpenSreHeader -Channel release -RequestedVersion '' -InstallDir 'C:\\opensre' -Repo 'Tracer-Cloud/opensre'
        Invoke-OpenSreStep -Name 'Unit progress step' -Operation {{ 'result-value' }}
        Write-OpenSreProgressLine -Label 'opensre_main_windows-arm64.zip.sha256' -DownloadedBytes 10 -TotalBytes 100
        Clear-OpenSreProgressLine
        """
    )

    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "OpenSRE installer" in output
    assert "Unit progress step" in output
    assert "OK Unit progress step" in output
    assert "result-value" in output
