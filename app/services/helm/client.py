"""Helm CLI client — read-only release inspection for investigations."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.integrations.config_models import HelmIntegrationConfig
from app.integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_CMD_TIMEOUT = 90.0
_PROBE_LIST_TIMEOUT = 45.0
_PROBE_VERSION_TIMEOUT = 15.0
_MAX_MANIFEST_CHARS = 600_000


class HelmClient:
    """Runs Helm 3 CLI commands with explicit kubeconfig/context and timeouts."""

    def __init__(self, config: HelmIntegrationConfig) -> None:
        self._config = config

    @property
    def is_configured(self) -> bool:
        return self._resolved_helm_path() is not None

    def _resolved_helm_path(self) -> str | None:
        raw = (self._config.helm_path or "helm").strip() or "helm"
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return str(candidate)
        return shutil.which(raw)

    def _kube_flags(self) -> list[str]:
        flags: list[str] = []
        ctx = self._config.kube_context.strip()
        if ctx:
            flags.extend(["--kube-context", ctx])
        kc = self._config.kubeconfig.strip()
        if kc:
            flags.extend(["--kubeconfig", str(Path(kc).expanduser())])
        return flags

    def _base_cmd(self) -> list[str] | None:
        hp = self._resolved_helm_path()
        if hp is None:
            return None
        return [hp, *self._kube_flags()]

    def _run(self, args: list[str], *, timeout: float) -> tuple[int, str, str]:
        base = self._base_cmd()
        if base is None:
            path_hint = (self._config.helm_path or "helm").strip() or "helm"
            return 127, "", f"helm executable not found ({path_hint!r})"
        cmd = [*base, *args]
        logger.debug("helm subprocess: %s subcommands", len(args))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            return 124, "", "helm command timed out"
        except OSError as exc:
            return 1, "", f"helm subprocess failed: {exc}"
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def probe_access(self) -> ProbeResult:
        if self._resolved_helm_path() is None:
            path = (self._config.helm_path or "helm").strip() or "helm"
            return ProbeResult.missing(
                f"Helm binary not found ({path!r}). Install Helm or set helm_path to a binary."
            )
        code, _, err = self._run(["version", "--client"], timeout=_PROBE_VERSION_TIMEOUT)
        if code != 0:
            detail = (err or "unknown error").strip()
            return ProbeResult.failed(f"helm version --client failed (exit {code}): {detail}")

        code, out, err = self._run(
            ["list", "-A", "--max", "1", "-o", "json"],
            timeout=_PROBE_LIST_TIMEOUT,
        )
        if code != 0:
            detail = (err or out or "cluster unreachable or kubeconfig missing").strip()
            return ProbeResult.failed(f"Helm cannot list releases: {detail}")

        stdout = (out or "").strip()
        if not stdout:
            return ProbeResult.failed(
                "Helm list returned empty output; expected a JSON array of releases."
            )
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            snippet = stdout[:200].replace("\n", " ")
            return ProbeResult.failed(
                f"Helm list output is not valid JSON ({exc}; stdout starts with {snippet!r})"
            )
        if not isinstance(parsed, list):
            return ProbeResult.failed(
                "Helm list -o json must return a JSON array of releases, "
                f"not {type(parsed).__name__}."
            )

        return ProbeResult.passed("Helm CLI is available and can reach the Kubernetes cluster.")

    def list_releases(
        self,
        *,
        namespace: str = "",
        all_namespaces: bool = False,
        max_releases: int = 256,
    ) -> dict[str, Any]:
        cap = max(1, min(max_releases, 4096))
        args = ["list", "-o", "json", "--max", str(cap)]
        if all_namespaces:
            args.append("-A")
        elif namespace.strip():
            args.extend(["-n", namespace.strip()])
        else:
            args.append("-A")

        code, out, err = self._run(args, timeout=_DEFAULT_CMD_TIMEOUT)
        if code != 0:
            return {
                "success": False,
                "error": (err or out).strip(),
                "releases": [],
                "all_namespaces": all_namespaces,
                "namespace": namespace.strip(),
            }
        try:
            parsed = json.loads(out or "[]")
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": "invalid JSON from helm list",
                "releases": [],
                "all_namespaces": all_namespaces,
                "namespace": namespace.strip(),
            }
        if not isinstance(parsed, list):
            return {
                "success": False,
                "error": "unexpected helm list shape",
                "releases": [],
                "all_namespaces": all_namespaces,
                "namespace": namespace.strip(),
            }
        return {
            "success": True,
            "error": "",
            "releases": parsed,
            "all_namespaces": all_namespaces,
            "namespace": namespace.strip(),
        }

    def release_status(self, release: str, namespace: str) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        if not rel:
            return {"success": False, "error": "release name is required", "status": {}}
        code, out, err = self._run(
            ["status", rel, "-n", ns, "-o", "json"],
            timeout=_DEFAULT_CMD_TIMEOUT,
        )
        if code != 0:
            return {"success": False, "error": (err or out).strip(), "status": {}}
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid JSON from helm status", "status": {}}
        if not isinstance(payload, dict):
            return {"success": False, "error": "unexpected helm status shape", "status": {}}
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "status": payload,
        }

    def release_history(
        self,
        release: str,
        namespace: str,
        *,
        max_revisions: int = 10,
    ) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        limit = max(1, min(max_revisions, 64))
        if not rel:
            return {"success": False, "error": "release name is required", "history": []}
        code, out, err = self._run(
            ["history", rel, "-n", ns, "-o", "json", "--max", str(limit)],
            timeout=_DEFAULT_CMD_TIMEOUT,
        )
        if code != 0:
            return {"success": False, "error": (err or out).strip(), "history": []}
        try:
            parsed = json.loads(out or "[]")
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid JSON from helm history", "history": []}
        if not isinstance(parsed, list):
            return {"success": False, "error": "unexpected helm history shape", "history": []}
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "history": parsed,
        }

    def get_values(
        self,
        release: str,
        namespace: str,
        *,
        all_values: bool = False,
    ) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        if not rel:
            return {"success": False, "error": "release name is required", "values": {}}
        args = ["get", "values", rel, "-n", ns, "-o", "json"]
        if all_values:
            args.append("--all")
        code, out, err = self._run(args, timeout=_DEFAULT_CMD_TIMEOUT)
        if code != 0:
            return {"success": False, "error": (err or out).strip(), "values": {}}
        try:
            raw = json.loads(out or "{}")
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid JSON from helm get values", "values": {}}
        # `helm get values -o json` emits JSON null for releases with no user values.
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            return {"success": False, "error": "unexpected helm values shape", "values": {}}
        parsed = raw
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "values": parsed,
            "all_values": all_values,
        }

    def get_manifest(self, release: str, namespace: str) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        if not rel:
            return {"success": False, "error": "release name is required", "manifest": ""}
        code, out, err = self._run(
            ["get", "manifest", rel, "-n", ns],
            timeout=_DEFAULT_CMD_TIMEOUT,
        )
        if code != 0:
            return {
                "success": False,
                "error": (err or out).strip(),
                "manifest": "",
                "truncated": False,
            }
        text = out or ""
        truncated = False
        if len(text) > _MAX_MANIFEST_CHARS:
            text = text[:_MAX_MANIFEST_CHARS]
            truncated = True
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "manifest": text,
            "truncated": truncated,
        }
