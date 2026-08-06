"""Offline bundle validation shared by its builder and Harbor installer."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.benchmarks.orcabench.config import BuildManifest

MANIFEST_NAME = "build-manifest.json"
WHEELHOUSE_NAME = "wheelhouse"


def file_sha256(path: Path) -> str:
    """Hash a file without loading a wheel into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_bundle(bundle_path: Path) -> BuildManifest:
    """Validate the manifest, required wheel, paths, and every recorded hash."""
    root = bundle_path.expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    wheelhouse = root / WHEELHOUSE_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"offline bundle manifest is missing: {manifest_path}")
    if not wheelhouse.is_dir():
        raise FileNotFoundError(f"offline wheelhouse is missing: {wheelhouse}")

    manifest = BuildManifest.from_path(manifest_path)
    wheel_path = _safe_member(root, manifest.opensre_wheel)
    for relative_name in manifest.files_sha256:
        _safe_member(root, relative_name)

    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"offline bundle must not contain symlinks: {path}")
        if path.is_file():
            actual_files.add(str(path.relative_to(root)))
    expected_files = set(manifest.files_sha256) | {MANIFEST_NAME}
    if actual_files != expected_files:
        raise ValueError(
            "offline bundle contents do not match its manifest: "
            f"unexpected={sorted(actual_files - expected_files)}, "
            f"missing={sorted(expected_files - actual_files)}"
        )

    if not wheel_path.is_file():
        raise FileNotFoundError(f"OpenSRE wheel is missing: {wheel_path}")

    for relative_name, expected in manifest.files_sha256.items():
        path = _safe_member(root, relative_name)
        if not path.is_file():
            raise FileNotFoundError(f"manifest member is missing: {path}")
        actual = file_sha256(path)
        if actual != expected:
            raise ValueError(
                f"bundle hash mismatch for {relative_name}: expected {expected}, got {actual}"
            )
    return manifest


def _safe_member(root: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"bundle member must be a safe relative path: {relative_name!r}")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"bundle member escapes bundle root: {relative_name!r}")
    return candidate
