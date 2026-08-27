#!/usr/bin/env python3
"""Build the Yandex Cloud read-API index from the official protobuf definitions.

Yandex generates its REST API from the protos in yandex-cloud/cloudapi, and every
method carries its REST binding as a ``google.api.http`` option. Extracting the
``get:`` bindings therefore yields the authoritative list of everything that can
be read — not a hand-maintained guess that drifts as services ship.

Run after cloning the repo:

    git clone --depth 1 https://github.com/yandex-cloud/cloudapi
    python integrations/yandex_cloud/build_api_index.py cloudapi

The output replaces ``integrations/yandex_cloud/api_index.json`` and records which
cloudapi commit produced it, so how stale the index is can be answered without
guessing. Only ``get:`` bindings are kept: the agent has no mutating path, so
shipping write endpoints would only invite it to try one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RPC = re.compile(
    r"rpc\s+(\w+)\s*\(([\w.]+)\)\s*returns\s*\([^)]*\)\s*\{(.*?)\n\s*\}",
    re.DOTALL,
)
_MESSAGE = re.compile(r"^message\s+(\w+)\s*\{", re.MULTILINE)
_FIELD = re.compile(r"^\s*(?:repeated\s+)?([\w.]+)\s+(\w+)\s*=\s*\d+\s*(\[[^;]*\])?;", re.MULTILINE)
_ENUM = re.compile(r"enum\s+(\w+)\s*\{(.*?)\n\s*\}", re.DOTALL)
_ENUM_VALUE = re.compile(r"^\s*(\w+)\s*=\s*\d+;", re.MULTILINE)
#: Paging is uniform across the API and adds nothing to a per-endpoint listing.
_UNINTERESTING = frozenset({"page_size", "page_token", "folder_id", "cluster_id"})
_GET = re.compile(r'get:\s*"([^"]+)"')
#: A ``get:`` binding is not by itself proof that a method only reads.
#: ``OperationService.Cancel`` is bound to GET ``/operations/{id}:cancel`` and
#: cancelling a running operation is a mutation, so the verb has to be checked
#: too or a write path lands in an index whose whole purpose is to hold none.
_MUTATING_RPC_VERBS: Final = (
    "Cancel",
    "Create",
    "Delete",
    "Deploy",
    "Disable",
    "Enable",
    "Move",
    "Rebalance",
    "Reset",
    "Restart",
    "Rollback",
    "Start",
    "Stop",
    "Update",
    "Upsert",
)


def _is_mutating(rpc_name: str) -> bool:
    """Whether the method changes something, regardless of its HTTP binding."""
    return any(rpc_name.startswith(verb) for verb in _MUTATING_RPC_VERBS)


_PACKAGE = re.compile(r"^package\s+([\w.]+);", re.MULTILINE)
_SERVICE = re.compile(r"^service\s+(\w+)\s*\{", re.MULTILINE)

_SOURCE_URL = "https://github.com/yandex-cloud/cloudapi"
_INDEX_PATH = _REPO_ROOT / "integrations/yandex_cloud/api_index.json"


def _is_version(segment: str) -> bool:
    return bool(re.fullmatch(r"v\d+\w*", segment))


#: Proto package fragment to endpoint-registry name, where the two disagree.
#:
#: The registry hyphenates and pluralises to its own taste and the protos do
#: not, so joining package parts finds nothing for these. They are listed rather
#: than guessed at: a heuristic that turned ``smartwebsecurity`` into
#: ``smart-web-security`` would have to know where the words break, and getting
#: it wrong silently drops endpoints instead of failing.
_REGISTRY_ALIASES: Final[dict[str, str]] = {
    "clouddesktop": "clouddesktops",
    "connectionmanager": "connection-manager",
    "serverless-mcpgateway": "serverless-mcp-gateway",
    "smartcaptcha": "smart-captcha",
    "smartwebsecurity": "smart-web-security",
    "ytsaurus": "managed-ytsaurus",
}


def _endpoint_key(path: str, package: str) -> str:
    """Resolve which endpoint host serves *path*, or "" when nothing does.

    The first path segment names the service for most of the API. Where it does
    not — serverless splits ``/functions`` and ``/containers`` across one
    ``serverless-*`` host each, managed databases share the ``/mdb`` prefix —
    the proto package carries the split, so it is tried next.
    """
    from integrations.yandex_cloud.endpoints import resolve_endpoint

    prefix = path.strip("/").split("/")[0]
    if resolve_endpoint(prefix):
        return prefix

    parts = [p for p in package.split(".") if p not in {"yandex", "cloud"} and not _is_version(p)]
    candidates = ("-".join(parts), "-".join(parts[:2]), parts[0] if parts else "")
    for candidate in candidates:
        if candidate and resolve_endpoint(candidate):
            return candidate
    for candidate in candidates:
        aliased = _REGISTRY_ALIASES.get(candidate, "")
        if aliased and resolve_endpoint(aliased):
            return aliased
    return ""


def _snake_to_camel(name: str) -> str:
    """REST takes the camelCase spelling of a proto field: ``service_type`` → ``serviceType``."""
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _message_body(text: str, name: str) -> str:
    """Return the body of ``message <name>``, or "" when it is declared elsewhere."""
    for match in _MESSAGE.finditer(text):
        if match.group(1) != name:
            continue
        depth, start = 0, match.end() - 1
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : index]
    return ""


def _enum_values(body: str, type_name: str) -> list[str]:
    """Allowed values for an enum declared inside the request message."""
    for match in _ENUM.finditer(body):
        if match.group(1) == type_name:
            return [
                value
                for value in _ENUM_VALUE.findall(match.group(2))
                if not value.endswith("_UNSPECIFIED")
            ]
    return []


def _parameters(text: str, request_type: str, path: str) -> list[dict[str, Any]]:
    """Query parameters a caller must supply, beyond what the path already carries.

    A path on its own is half an answer: ``/managed-postgresql/v1/clusters/{id}:logs``
    answers ``unknown service type`` until ``serviceType`` is passed, and nothing in
    the path hints at that. The proto knows, so the index carries it.
    """
    body = _message_body(text, request_type.rsplit(".", 1)[-1])
    if not body:
        return []

    parameters: list[dict[str, Any]] = []
    for field_match in _FIELD.finditer(body):
        field_type, field_name, options = field_match.groups()
        if field_name in _UNINTERESTING or "{" + field_name + "}" in path:
            continue
        entry: dict[str, Any] = {"name": _snake_to_camel(field_name)}
        if options and "(required) = true" in options:
            entry["required"] = True
        values = _enum_values(body, field_type)
        if values:
            entry["values"] = values
        parameters.append(entry)
    # Required first: they are the difference between a call and a 400.
    parameters.sort(key=lambda item: (not item.get("required"), item["name"]))
    return parameters


def _resource(path: str) -> str:
    """Return the resource a path reads, e.g. ``clusters`` for a cluster get."""
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    return segments[-1] if len(segments) > 1 else ""


def collect(cloudapi_root: Path) -> list[dict[str, Any]]:
    """Return every GET binding in the protos under *cloudapi_root*."""
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    dropped: dict[str, int] = {"mutating": 0, "no_host": 0}
    unhosted: set[str] = set()

    for proto in sorted((cloudapi_root / "yandex").rglob("*.proto")):
        text = proto.read_text(encoding="utf-8", errors="replace")
        package_match = _PACKAGE.search(text)
        package = package_match.group(1) if package_match else ""
        service_match = _SERVICE.search(text)
        grpc_service = service_match.group(1) if service_match else ""

        for rpc_match in _RPC.finditer(text):
            rpc_name, request_type, body = rpc_match.groups()
            get_match = _GET.search(body)
            if not get_match:
                continue
            if _is_mutating(rpc_name):
                dropped["mutating"] += 1
                continue
            path = get_match.group(1)
            key = _endpoint_key(path, package)
            if not key:
                # No reachable host: the service is not in the registry. Counted
                # rather than passed over in silence - this is the path that once
                # hid 34 endpoints behind a naming mismatch.
                dropped["no_host"] += 1
                unhosted.add(package)
                continue
            if (key, path) in seen:
                continue
            seen.add((key, path))
            entry: dict[str, Any] = {
                "service": key,
                "path": path,
                "rpc": f"{grpc_service}.{rpc_name}" if grpc_service else rpc_name,
                "resource": _resource(path),
                "package": package,
            }
            parameters = _parameters(text, request_type, path)
            if parameters:
                entry["params"] = parameters
            entries.append(entry)

    entries.sort(key=lambda e: (e["service"], e["path"]))
    # Say what was left out. A generator that silently drops bindings is how 34
    # endpoints once went missing behind a naming mismatch and looked, from the
    # outside, like Yandex not offering an API at all.
    print(
        f"kept {len(entries)} read endpoints; "
        f"dropped {dropped['mutating']} mutating and {dropped['no_host']} with no registry host"
    )
    for package in sorted(unhosted):
        print(f"  no host for package: {package}")
    return entries


def _git(cloudapi_root: Path, *args: str) -> str:
    """Return the output of a git command, or "" when it cannot be read.

    A checkout without git metadata — an unpacked tarball, say — is usable for
    generating the index; it only leaves the provenance thinner.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(cloudapi_root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _provenance(cloudapi_root: Path) -> dict[str, str]:
    """Record which cloudapi produced this index, and when it was built."""
    provenance = {
        "source": _SOURCE_URL,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    commit = _git(cloudapi_root, "rev-parse", "HEAD")
    if commit:
        provenance["commit"] = commit
    commit_date = _git(cloudapi_root, "log", "-1", "--format=%cs")
    if commit_date:
        provenance["commit_date"] = commit_date
    return provenance


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    cloudapi_root = Path(argv[1]).expanduser().resolve()
    if not (cloudapi_root / "yandex").is_dir():
        print(f"not a cloudapi checkout: {cloudapi_root}", file=sys.stderr)
        return 1

    entries = collect(cloudapi_root)
    services = sorted({entry["service"] for entry in entries})
    payload: dict[str, Any] = {**_provenance(cloudapi_root), "endpoints": entries}
    _INDEX_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(entries)} read endpoints across {len(services)} services -> {_INDEX_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
