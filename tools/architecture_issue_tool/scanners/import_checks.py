"""Import-layer violation scanning via import-linter and CI direct-edge checks."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.architecture_issue_tool.models import ArchitectureViolation, Severity, ViolationKind

_RULE_HEADER_RE = re.compile(r"^(.+) is not allowed to import (.+):$")
_EDGE_RE = re.compile(r"^\s*-\s*(.+?)\s*->\s*(.+?)\s*\(l\.(\d+)\)\s*$")
_DIRECT_EDGE_RE = re.compile(r"^\s{2}(.+?) -> (.+?)(?:\s+\(line (\d+)\))?\s*$")
_IGNORE_IMPORTS_RE = re.compile(r"^ignore_imports\s*=\s*\n(?:    .+\n)+", re.MULTILINE)

_P0_RULE_MARKERS = ("core", "config", "integrations")


@dataclass(frozen=True)
class ParsedImportEdge:
    rule: str
    source_module: str
    target_module: str
    line: int


def parse_lint_imports_output(stdout: str) -> list[ParsedImportEdge]:
    """Parse import-linter broken-contract stdout into structured edges."""
    edges: list[ParsedImportEdge] = []
    current_rule = ""
    pending_source = ""
    pending_target = ""
    pending_line = 0

    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("=") or re.fullmatch(r"-+", line):
            continue

        rule_match = _RULE_HEADER_RE.match(line)
        if rule_match:
            current_rule = line
            pending_source = ""
            continue

        edge_match = _EDGE_RE.match(line)
        if edge_match and current_rule:
            source_module = edge_match.group(1).strip()
            target_module = edge_match.group(2).strip()
            line_no = int(edge_match.group(3))
            edges.append(
                ParsedImportEdge(
                    rule=current_rule,
                    source_module=source_module,
                    target_module=target_module,
                    line=line_no,
                )
            )
            pending_source = ""
            continue

        if pending_source and line.startswith(" "):
            pending_target = f"{pending_target}{line.strip()}"
            edges.append(
                ParsedImportEdge(
                    rule=current_rule,
                    source_module=pending_source,
                    target_module=pending_target,
                    line=pending_line,
                )
            )
            pending_source = ""
            pending_target = ""
            continue

        if line.startswith("- ") and "->" in line and "(l." not in line:
            parts = line.removeprefix("- ").split("->", 1)
            if len(parts) == 2:
                pending_source = parts[0].strip()
                pending_target = parts[1].strip()
                pending_line = 0

    return edges


def parse_direct_imports_output(stdout: str) -> list[ParsedImportEdge]:
    """Parse check_direct_imports.py failure stdout."""
    edges: list[ParsedImportEdge] = []
    current_section = ""

    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if "forbidden module-level direct import" in line:
            current_section = "module-level"
            continue
        if "forbidden nested direct import" in line:
            current_section = "nested"
            continue

        match = _DIRECT_EDGE_RE.match(line)
        if not match:
            continue

        source_module = match.group(1).strip()
        target_module = match.group(2).strip()
        line_no = int(match.group(3)) if match.group(3) else 0
        rule = (
            f"Forbidden nested direct import ({current_section})"
            if current_section == "nested"
            else "Forbidden module-level direct import"
        )
        edges.append(
            ParsedImportEdge(
                rule=rule,
                source_module=source_module,
                target_module=target_module,
                line=line_no,
            )
        )

    return edges


def _lint_imports_executable() -> str | None:
    found = shutil.which("lint-imports")
    if found:
        return found
    candidate = Path(sys.executable).with_name("lint-imports")
    if candidate.is_file():
        return str(candidate)
    return None


def _resolve_importlinter_config(clone_root: Path, *, strict_layers: bool) -> Path | None:
    if strict_layers:
        strict_config = clone_root / ".importlinter.strict"
        if strict_config.is_file():
            return strict_config
    default_config = clone_root / ".importlinter"
    if default_config.is_file():
        return default_config
    return None


def _config_without_baselines(config_path: Path) -> Path:
    text = config_path.read_text(encoding="utf-8")
    stripped = _IGNORE_IMPORTS_RE.sub("ignore_imports =\n", text)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=config_path.name,
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(stripped)
        handle.flush()
        return Path(handle.name)


def _run_lint_imports(clone_root: Path, config_path: Path) -> subprocess.CompletedProcess[str]:
    executable = _lint_imports_executable()
    if executable is None:
        raise FileNotFoundError("lint-imports")

    return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [executable, "--config", str(config_path)],
        cwd=clone_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _severity_for_rule(rule: str) -> Severity:
    lowered = rule.lower()
    if any(marker in lowered for marker in _P0_RULE_MARKERS):
        return "p0"
    return "p1"


def _violation_id(kind: str, source_module: str, target_module: str) -> str:
    digest = hashlib.sha256(f"{kind}:{source_module}->{target_module}".encode()).hexdigest()
    return f"{kind[:1]}-{digest[:12]}"


def _edge_to_violation(edge: ParsedImportEdge, *, kind: ViolationKind) -> ArchitectureViolation:
    edge_label = f"{edge.source_module} -> {edge.target_module}"
    return ArchitectureViolation(
        id=_violation_id(kind, edge.source_module, edge.target_module),
        kind=kind,
        severity=_severity_for_rule(edge.rule),
        title=edge.rule,
        evidence={
            "source_module": edge.source_module,
            "target_module": edge.target_module,
            "line": edge.line,
            "rule": edge.rule,
            "edge": edge_label,
        },
        fix_direction=(
            f"Remove or refactor the import edge {edge_label} so it respects the "
            f"repository layer contract described by: {edge.rule}"
        ),
    )


def _scan_layer_imports(
    clone_root: Path,
    *,
    strict_layers: bool,
    include_baselines: bool,
) -> tuple[list[ArchitectureViolation], list[str]]:
    warnings: list[str] = []
    config_path = _resolve_importlinter_config(clone_root, strict_layers=strict_layers)
    if config_path is None:
        return [], ["no import-linter config found in cloned repository"]

    temp_config: Path | None = None
    effective_config = config_path
    if include_baselines:
        temp_config = _config_without_baselines(config_path)
        effective_config = temp_config

    try:
        try:
            completed = _run_lint_imports(clone_root, effective_config)
        except FileNotFoundError:
            return [], ["lint-imports is not installed; skipped layer import checks"]

        if completed.returncode == 0 and not include_baselines:
            return [], warnings

        edges = parse_lint_imports_output(completed.stdout)
        if completed.returncode != 0 and not edges:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if detail:
                warnings.append(f"import-linter failed without parseable edges: {detail[:200]}")
            return [], warnings

        return [_edge_to_violation(edge, kind="layer_import") for edge in edges], warnings
    finally:
        if temp_config is not None and temp_config.exists():
            temp_config.unlink(missing_ok=True)


def _scan_direct_imports(clone_root: Path) -> tuple[list[ArchitectureViolation], list[str]]:
    script = clone_root / ".github" / "ci" / "check_direct_imports.py"
    if not script.is_file():
        return [], []

    completed = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [sys.executable, str(script)],
        cwd=clone_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return [], []

    edges = parse_direct_imports_output(completed.stdout)
    return [_edge_to_violation(edge, kind="direct_import") for edge in edges], []


def scan_import_violations(
    clone_root: Path,
    *,
    strict_layers: bool = True,
    include_baselines: bool = False,
) -> tuple[list[ArchitectureViolation], list[str]]:
    """Scan *clone_root* for layer and direct import violations."""
    warnings: list[str] = []
    violations: list[ArchitectureViolation] = []

    layer_violations, layer_warnings = _scan_layer_imports(
        clone_root,
        strict_layers=strict_layers,
        include_baselines=include_baselines,
    )
    violations.extend(layer_violations)
    warnings.extend(layer_warnings)

    direct_violations, direct_warnings = _scan_direct_imports(clone_root)
    violations.extend(direct_violations)
    warnings.extend(direct_warnings)

    return violations, warnings
