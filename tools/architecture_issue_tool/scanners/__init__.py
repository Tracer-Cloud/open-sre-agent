"""Architecture violation scanners for cloned repository workspaces."""

from __future__ import annotations

from tools.architecture_issue_tool.scanners.compatibility_shims import scan_compatibility_shims
from tools.architecture_issue_tool.scanners.import_checks import scan_import_violations
from tools.architecture_issue_tool.scanners.module_placement import scan_module_placement
from tools.architecture_issue_tool.scanners.oversized_files import scan_oversized_files

__all__ = [
    "scan_compatibility_shims",
    "scan_import_violations",
    "scan_module_placement",
    "scan_oversized_files",
]
