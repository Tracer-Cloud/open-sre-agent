"""Real filesystem builders shared by ORCA benchmark tests."""

from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.orcabench.host.bundle import MANIFEST_NAME, file_sha256


def create_bundle(root: Path) -> Path:
    """Create the smallest hash-valid offline bundle for boundary tests."""
    bundle = root / "bundle"
    wheelhouse = bundle / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    wheel = wheelhouse / "opensre-0.1-py3-none-any.whl"
    wheel.write_bytes(b"not-installed-by-this-test")
    requirements = bundle / "requirements.txt"
    requirements.write_text("", encoding="utf-8")
    files = (wheel, requirements)
    manifest = {
        "schema_version": 1,
        "opensre_commit": "1234567890abcdef",
        "dirty_files": [],
        "python_version": "3.13.0",
        "opensre_wheel": str(wheel.relative_to(bundle)),
        "files_sha256": {str(path.relative_to(bundle)): file_sha256(path) for path in files},
    }
    (bundle / MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return bundle
