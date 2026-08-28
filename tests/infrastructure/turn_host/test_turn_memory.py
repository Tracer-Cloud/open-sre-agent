"""Resident-memory helpers used to size turn concurrency."""

from __future__ import annotations

import subprocess
import sys

from infrastructure.turn_host.turn_memory import (
    peak_resident_memory_bytes,
    resident_memory_bytes,
)


def test_resident_memory_bytes_is_a_positive_measurement_or_none() -> None:
    # Contract: a real byte count where the platform exposes it, else None —
    # never zero, negative, or a raised error the turn host would have to catch.
    memory = resident_memory_bytes()
    assert memory is None or memory > 0


def test_peak_resident_memory_bytes_is_a_positive_measurement_or_none() -> None:
    peak = peak_resident_memory_bytes()
    assert peak is None or peak > 0


def test_import_and_sampling_succeed_without_resource_module() -> None:
    script = """
import sys

sys.modules["resource"] = None

from infrastructure.turn_host.turn_memory import (
    peak_resident_memory_bytes,
    resident_memory_bytes,
)

assert resident_memory_bytes() is None
assert peak_resident_memory_bytes() is None
"""
    subprocess.run([sys.executable, "-c", script], check=True)
