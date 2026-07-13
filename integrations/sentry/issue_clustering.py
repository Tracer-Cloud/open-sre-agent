"""Structural clustering and human-readable labels for Sentry issue groups."""

from __future__ import annotations

import re
from typing import Any

_TITLE_THEME_RE = re.compile(r"^\[([^\]]+)\]")
_CULPRIT_KEY_RE = re.compile(r"[^a-z0-9._-]+")

# Overrides where a generic package label would be wrong or too vague.
# Longest matching prefix wins (e.g. integrations.eks.* → EKS / Kubernetes).
STRUCTURAL_LABEL_OVERRIDES: dict[str, str] = {
    "integrations.eks": "EKS / Kubernetes errors",
    "integrations.cloudtrail": "CloudTrail / AWS errors",
    "core.llm": "LLM runtime / provider errors",
    "core.agent": "Agent runtime errors",
    "tools.investigation": "Investigation pipeline errors",
    "surfaces.cli": "CLI surface errors",
    "surfaces.interactive_shell": "Interactive shell errors",
    "platform.harness_ports": "Harness / integration wiring errors",
    "uncategorised": "Uncategorised errors",
}


def _culprit_module(culprit: str) -> str:
    text = culprit.strip()
    if " in " in text:
        return text.split(" in ", 1)[0].strip()
    return text


def _sanitize_key(text: str) -> str:
    cleaned = _CULPRIT_KEY_RE.sub("_", text.lower()).strip("._")
    return cleaned or "unknown"


def _package_cluster_key(module: str, *, depth: int) -> str:
    parts = [part for part in module.split(".") if part]
    if not parts:
        return "uncategorised"
    return ".".join(parts[:depth])


def _title_theme_key(issue: dict[str, Any]) -> str | None:
    title = str(issue.get("title") or "").strip()
    match = _TITLE_THEME_RE.match(title)
    if not match:
        return None
    theme = _sanitize_key(match.group(1))
    return f"title-theme:{theme}" if theme != "unknown" else None


def structural_cluster_key_for_issue(issue: dict[str, Any]) -> str:
    """Assign a stable structural bucket from culprit, title theme, or issue id."""
    module = _culprit_module(str(issue.get("culprit") or ""))

    if module.startswith("integrations."):
        return _package_cluster_key(module, depth=3 if module.count(".") >= 2 else 2)

    if module.startswith("tools."):
        return _package_cluster_key(module, depth=3 if module.count(".") >= 2 else 2)

    for prefix in ("core.", "surfaces.", "platform.", "gateway."):
        if module.startswith(prefix):
            return _package_cluster_key(module, depth=2)

    if module and "." in module:
        return f"culprit:{_sanitize_key(module)}"

    title_theme = _title_theme_key(issue)
    if title_theme is not None:
        return title_theme

    short_id = str(issue.get("shortId") or "")
    if "-" in short_id:
        return f"issue-group:{short_id.rsplit('-', 1)[0].lower()}"

    project = issue.get("project")
    if isinstance(project, dict):
        slug = str(project.get("slug") or "").strip()
        if slug:
            return f"project:{slug}"
    elif isinstance(project, str) and project.strip():
        return f"project:{project.strip()}"

    if module:
        return f"culprit:{_sanitize_key(module)}"

    return "uncategorised"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _package_title(package: str) -> str:
    return package.replace("_", " ").title()


def _structural_label_override(key: str) -> str | None:
    if key in STRUCTURAL_LABEL_OVERRIDES:
        return STRUCTURAL_LABEL_OVERRIDES[key]
    best_label: str | None = None
    best_prefix_len = -1
    for prefix, label in STRUCTURAL_LABEL_OVERRIDES.items():
        if (key == prefix or key.startswith(f"{prefix}.")) and len(prefix) > best_prefix_len:
            best_label = label
            best_prefix_len = len(prefix)
    return best_label


def _generic_structural_label(key: str) -> str:
    if key.startswith("integrations."):
        vendor = key.removeprefix("integrations.").split(".", 1)[0]
        return f"{_package_title(vendor)} integration errors"
    if key.startswith("tools."):
        package = key.removeprefix("tools.").split(".", 1)[0]
        return f"{_package_title(package)} tool errors"
    if key.startswith("core."):
        package = key.removeprefix("core.").split(".", 1)[0]
        return f"{_package_title(package)} runtime errors"
    if key.startswith("surfaces."):
        package = key.removeprefix("surfaces.").split(".", 1)[0]
        return f"{_package_title(package)} surface errors"
    if key.startswith("platform."):
        package = key.removeprefix("platform.").split(".", 1)[0]
        return f"{_package_title(package)} platform errors"
    if key.startswith("title-theme:"):
        theme = key.removeprefix("title-theme:").replace("_", " ")
        return f"{theme.title()} errors (from issue titles)"
    if key.startswith("culprit:"):
        return f"Code path {key.removeprefix('culprit:').replace('_', '.')}"
    if key.startswith("project:"):
        slug = key.removeprefix("project:")
        return f"Sentry project {slug} (fallback bucket — inspect samples)"
    if key.startswith("issue-group:"):
        return f"Issue family {key.removeprefix('issue-group:').upper()}"
    return key


def structural_cluster_label(key: str, *, sample_titles: tuple[str, ...] = ()) -> str:
    """Map a structural cluster key to a human-readable label for summaries."""
    base = _structural_label_override(key) or _generic_structural_label(key)
    if sample_titles:
        return f"{base} — e.g. {_truncate(sample_titles[0], 72)}"
    return base


# Backward-compatible alias used by older tests/callers.
cluster_name_for_issue = structural_cluster_key_for_issue
