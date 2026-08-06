from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.orcabench.host.bundle import validate_bundle
from tests.benchmarks.orcabench.tests._support import create_bundle


def test_validate_bundle_checks_every_recorded_hash(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path)
    manifest = validate_bundle(bundle)

    assert manifest.opensre_wheel == "wheelhouse/opensre-0.1-py3-none-any.whl"

    (bundle / manifest.opensre_wheel).write_bytes(b"changed")
    with pytest.raises(ValueError, match="bundle hash mismatch"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_manifest_path_escape(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path)
    manifest_path = bundle / "build-manifest.json"
    content = manifest_path.read_text(encoding="utf-8").replace(
        "wheelhouse/opensre-0.1-py3-none-any.whl",
        "../opensre.whl",
        1,
    )
    manifest_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="safe relative path"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_unrecorded_files(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path)
    (bundle / "unexpected.txt").write_text("not in manifest", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected=.*unexpected.txt"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_symlinks(tmp_path: Path) -> None:
    bundle = create_bundle(tmp_path)
    (bundle / "linked-wheel").symlink_to("wheelhouse/opensre-0.1-py3-none-any.whl")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        validate_bundle(bundle)
