"""Stage the official ORCA snapshot image into a validated host cache."""

from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_REQUIRED_MEMBERS = (
    "opentelemetry-demo",
    "data",
    "docker-compose-base.yml",
    "docker-compose.snapshot.yml",
    "otelcol-config-snapshot.yml",
    "jaeger-config-snapshot.yml",
)
_CACHE_MANIFEST = "opensre-orca-snapshot.json"


def _docker(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ("docker", *args),
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def _image_id(image: str) -> str:
    try:
        return _docker("image", "inspect", "--format", "{{.Id}}", image, capture=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"snapshot image is not available locally: {image}. Pull it before staging."
        ) from exc


def _validate_cache(path: Path, image: str, image_id: str) -> bool:
    manifest_path = path / _CACHE_MANIFEST
    if not manifest_path.is_file():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest != {"image": image, "image_id": image_id}:
        return False
    return all((path / member).exists() for member in _REQUIRED_MEMBERS)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def stage_snapshot(image: str, cache_root: Path) -> Path:
    """Copy `/app` from one local image into a reusable, image-ID-keyed cache."""
    image_id = _image_id(image)
    key = image_id.removeprefix("sha256:")[:24]
    root = cache_root.expanduser().resolve()
    destination = root / key
    lock_path = root / ".stage.lock"

    with _exclusive_lock(lock_path):
        if _validate_cache(destination, image, image_id):
            return destination
        if destination.exists():
            raise RuntimeError(
                f"snapshot cache exists but is incomplete or stale: {destination}. "
                "Move it aside and stage again."
            )

        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temporary_name:
            temporary = Path(temporary_name)
            container_name = f"opensre-orca-stage-{uuid.uuid4().hex}"
            container_id = ""
            try:
                container_id = _docker("create", "--name", container_name, image, capture=True)
                _docker("cp", f"{container_id}:/app/.", str(temporary))
            finally:
                if container_id:
                    subprocess.run(
                        ("docker", "rm", "-f", container_id),
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

            missing = [member for member in _REQUIRED_MEMBERS if not (temporary / member).exists()]
            if missing:
                raise RuntimeError(f"snapshot image /app is missing required members: {missing}")
            (temporary / _CACHE_MANIFEST).write_text(
                json.dumps({"image": image, "image_id": image_id}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.rename(destination)
    return destination
