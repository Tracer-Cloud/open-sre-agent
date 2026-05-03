"""Unit tests for app/cli/local_llm/hardware.py

Covers:
- _get_total_ram_gb()   — Linux /proc/meminfo, macOS sysctl, and fallback
- _get_available_ram_gb() — Linux /proc/meminfo, macOS sysctl, and fallback
- detect_hardware()     — full profile assembly incl. NVIDIA detection
All tests are platform-independent: the real host OS is never consulted.
"""

from __future__ import annotations

import sys
from unittest.mock import mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 16 GiB expressed in the units each interface produces
_16_GiB_BYTES = 16 * 1024**3  # macOS sysctl (bytes as string)
_8_GiB_BYTES = 8 * 1024**3
_16_GiB_KB = 16 * 1024**2  # Linux /proc/meminfo (kB)
_10_GiB_KB = 10 * 1024**2

_16_GiB = 16.0
_10_GiB = 10.0
_8_GiB = 8.0

LINUX_MEMINFO_16G_10G = (
    f"MemTotal:       {_16_GiB_KB} kB\n"
    "MemFree:        2097152 kB\n"
    f"MemAvailable:   {_10_GiB_KB} kB\n"
    "Buffers:        123456 kB\n"
)

MODULE = "app.cli.local_llm.hardware"


# ---------------------------------------------------------------------------
# _get_total_ram_gb — Linux
# ---------------------------------------------------------------------------


class TestGetTotalRamGbLinux:
    def _call(self) -> float:
        from app.cli.local_llm.hardware import _get_total_ram_gb

        return _get_total_ram_gb()

    def test_parses_memtotal_correctly(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", mock_open(read_data=LINUX_MEMINFO_16G_10G)),
        ):
            mock_sys.platform = "linux"
            result = self._call()
        assert result == pytest.approx(_16_GiB)

    def test_returns_fallback_on_open_failure(self) -> None:
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB

        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", side_effect=OSError("no file")),
        ):
            mock_sys.platform = "linux"
            result = self._call()
        assert result == _FALLBACK_RAM_GB

    def test_returns_fallback_on_parse_failure(self) -> None:
        """MemTotal line present but value is non-numeric."""
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB

        bad_meminfo = "MemTotal:       GARBAGE kB\n"
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", mock_open(read_data=bad_meminfo)),
        ):
            mock_sys.platform = "linux"
            result = self._call()
        assert result == _FALLBACK_RAM_GB

    def test_returns_fallback_when_memtotal_missing(self) -> None:
        """File readable but contains no MemTotal line."""
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB

        meminfo_no_total = "MemFree:        8388608 kB\nMemAvailable:   7000000 kB\n"
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", mock_open(read_data=meminfo_no_total)),
        ):
            mock_sys.platform = "linux"
            result = self._call()
        assert result == _FALLBACK_RAM_GB


# ---------------------------------------------------------------------------
# _get_total_ram_gb — macOS
# ---------------------------------------------------------------------------


class TestGetTotalRamGbMacOS:
    def _call(self) -> float:
        from app.cli.local_llm.hardware import _get_total_ram_gb

        return _get_total_ram_gb()

    def test_parses_sysctl_output_correctly(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.subprocess.check_output", return_value=f"{_16_GiB_BYTES}\n"),
        ):
            mock_sys.platform = "darwin"
            result = self._call()
        assert result == pytest.approx(_16_GiB)

    def test_calls_correct_sysctl_key(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(
                f"{MODULE}.subprocess.check_output", return_value=f"{_16_GiB_BYTES}\n"
            ) as mock_sub,
        ):
            mock_sys.platform = "darwin"
            self._call()
        mock_sub.assert_called_once_with(["sysctl", "-n", "hw.memsize"], text=True)

    def test_returns_fallback_on_subprocess_failure(self) -> None:
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB

        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(
                f"{MODULE}.subprocess.check_output", side_effect=FileNotFoundError("sysctl missing")
            ),
        ):
            mock_sys.platform = "darwin"
            result = self._call()
        assert result == _FALLBACK_RAM_GB

    def test_returns_fallback_on_non_integer_output(self) -> None:
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB

        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.subprocess.check_output", return_value="not_a_number\n"),
        ):
            mock_sys.platform = "darwin"
            result = self._call()
        assert result == _FALLBACK_RAM_GB


# ---------------------------------------------------------------------------
# _get_available_ram_gb — Linux
# ---------------------------------------------------------------------------


class TestGetAvailableRamGbLinux:
    def _call(self, total: float) -> float:
        from app.cli.local_llm.hardware import _get_available_ram_gb

        return _get_available_ram_gb(total)

    def test_parses_memavailable_correctly(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", mock_open(read_data=LINUX_MEMINFO_16G_10G)),
        ):
            mock_sys.platform = "linux"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_10_GiB)

    def test_returns_half_total_on_open_failure(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", side_effect=OSError("no file")),
        ):
            mock_sys.platform = "linux"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_16_GiB * 0.5)

    def test_returns_half_total_when_memavailable_missing(self) -> None:
        meminfo_no_avail = "MemTotal:       16777216 kB\nMemFree:        2097152 kB\n"
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", mock_open(read_data=meminfo_no_avail)),
        ):
            mock_sys.platform = "linux"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_16_GiB * 0.5)

    def test_returns_half_total_on_parse_failure(self) -> None:
        bad_meminfo = "MemAvailable:   GARBAGE kB\n"
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch("builtins.open", mock_open(read_data=bad_meminfo)),
        ):
            mock_sys.platform = "linux"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_16_GiB * 0.5)


# ---------------------------------------------------------------------------
# _get_available_ram_gb — macOS
# ---------------------------------------------------------------------------


class TestGetAvailableRamGbMacOS:
    def _call(self, total: float) -> float:
        from app.cli.local_llm.hardware import _get_available_ram_gb

        return _get_available_ram_gb(total)

    def test_parses_sysctl_output_correctly(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.subprocess.check_output", return_value=f"{_8_GiB_BYTES}\n"),
        ):
            mock_sys.platform = "darwin"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_8_GiB)

    def test_calls_correct_sysctl_key(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(
                f"{MODULE}.subprocess.check_output", return_value=f"{_8_GiB_BYTES}\n"
            ) as mock_sub,
        ):
            mock_sys.platform = "darwin"
            self._call(_16_GiB)
        mock_sub.assert_called_once_with(["sysctl", "-n", "hw.usermem"], text=True)

    def test_returns_half_total_on_subprocess_failure(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.subprocess.check_output", side_effect=OSError("sysctl error")),
        ):
            mock_sys.platform = "darwin"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_16_GiB * 0.5)

    def test_returns_half_total_on_non_integer_output(self) -> None:
        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.subprocess.check_output", return_value="bad\n"),
        ):
            mock_sys.platform = "darwin"
            result = self._call(_16_GiB)
        assert result == pytest.approx(_16_GiB * 0.5)


# ---------------------------------------------------------------------------
# _get_available_ram_gb — unknown / Windows platform
# ---------------------------------------------------------------------------


class TestGetAvailableRamGbUnknownPlatform:
    def test_returns_half_total_on_unrecognised_platform(self) -> None:
        from app.cli.local_llm.hardware import _get_available_ram_gb

        with patch(f"{MODULE}.sys") as mock_sys:
            mock_sys.platform = "win32"
            result = _get_available_ram_gb(_16_GiB)
        assert result == pytest.approx(_16_GiB * 0.5)


class TestGetTotalRamGbUnknownPlatform:
    def test_returns_fallback_on_unrecognised_platform(self) -> None:
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB, _get_total_ram_gb

        with patch(f"{MODULE}.sys") as mock_sys:
            mock_sys.platform = "win32"
            result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB


# ---------------------------------------------------------------------------
# detect_hardware()
# ---------------------------------------------------------------------------


class TestDetectHardware:
    """detect_hardware() should assemble a correct HardwareProfile regardless of the host OS."""

    def _detect(
        self,
        *,
        platform_str: str,
        machine: str,
        total_gb: float,
        available_gb: float,
        nvidia: bool,
    ):
        from app.cli.local_llm.hardware import detect_hardware

        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.platform.machine", return_value=machine),
            patch(f"{MODULE}._get_total_ram_gb", return_value=total_gb),
            patch(f"{MODULE}._get_available_ram_gb", return_value=available_gb),
            patch(f"{MODULE}.shutil.which", return_value="/usr/bin/nvidia-smi" if nvidia else None),
        ):
            mock_sys.platform = platform_str
            return detect_hardware()

    # --- Apple Silicon ---

    def test_apple_silicon_profile(self) -> None:
        hw = self._detect(
            platform_str="darwin",
            machine="arm64",
            total_gb=_16_GiB,
            available_gb=_8_GiB,
            nvidia=False,
        )
        assert hw.is_apple_silicon is True
        assert hw.has_nvidia_gpu is False
        assert hw.total_ram_gb == pytest.approx(_16_GiB)
        assert hw.available_ram_gb == pytest.approx(_8_GiB)
        assert hw.arch == "arm64"

    # --- macOS Intel (darwin, x86_64) ---

    def test_macos_intel_profile(self) -> None:
        hw = self._detect(
            platform_str="darwin",
            machine="x86_64",
            total_gb=_16_GiB,
            available_gb=_8_GiB,
            nvidia=False,
        )
        assert hw.is_apple_silicon is False
        assert hw.arch == "x86_64"

    # --- Linux with NVIDIA ---

    def test_linux_with_nvidia_gpu(self) -> None:
        hw = self._detect(
            platform_str="linux",
            machine="x86_64",
            total_gb=32.0,
            available_gb=24.0,
            nvidia=True,
        )
        assert hw.is_apple_silicon is False
        assert hw.has_nvidia_gpu is True
        assert hw.total_ram_gb == pytest.approx(32.0)
        assert hw.available_ram_gb == pytest.approx(24.0)

    # --- Linux without NVIDIA ---

    def test_linux_without_nvidia_gpu(self) -> None:
        hw = self._detect(
            platform_str="linux",
            machine="x86_64",
            total_gb=_8_GiB,
            available_gb=4.0,
            nvidia=False,
        )
        assert hw.has_nvidia_gpu is False

    # --- shutil.which is actually called with "nvidia-smi" ---

    def test_nvidia_detection_queries_nvidia_smi(self) -> None:
        from app.cli.local_llm.hardware import detect_hardware

        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.platform.machine", return_value="x86_64"),
            patch(f"{MODULE}._get_total_ram_gb", return_value=_8_GiB),
            patch(f"{MODULE}._get_available_ram_gb", return_value=4.0),
            patch(f"{MODULE}.shutil.which", return_value=None) as mock_which,
        ):
            mock_sys.platform = "linux"
            detect_hardware()
        mock_which.assert_called_once_with("nvidia-smi")

    # --- Conservative fallback RAM propagates into profile ---

    def test_fallback_ram_propagates(self) -> None:
        from app.cli.local_llm.hardware import _FALLBACK_RAM_GB, detect_hardware

        with (
            patch(f"{MODULE}.sys") as mock_sys,
            patch(f"{MODULE}.platform.machine", return_value="x86_64"),
            patch(f"{MODULE}._get_total_ram_gb", return_value=_FALLBACK_RAM_GB),
            patch(f"{MODULE}._get_available_ram_gb", return_value=_FALLBACK_RAM_GB * 0.5),
            patch(f"{MODULE}.shutil.which", return_value=None),
        ):
            mock_sys.platform = "linux"
            hw = detect_hardware()
        assert hw.total_ram_gb == pytest.approx(_FALLBACK_RAM_GB)
        assert hw.available_ram_gb == pytest.approx(_FALLBACK_RAM_GB * 0.5)
