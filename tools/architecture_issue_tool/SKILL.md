---
name: architecture-audit
description: Audit a GitHub repository for architecture violations and turn findings into atomic refactor tasks and optional GitHub issues.
tools:
  - find_architecture_violations
---

# Architecture Audit

1. Call `find_architecture_violations` with `owner`/`repo`.
2. Reply with the **full** Markdown report below — never a casual prose summary.
   Use tool JSON only. Render `scan_summary.hotspots` as the statistics table.
3. Propose tasks; never auto-apply fixes. File GitHub issues only after approval.

## Required reply template (fill every section)

### Executive summary
Counts from `scan_summary` (`violations`, `severity_counts`, `kind_counts`).
Note partial coverage when `coverage_complete` is false.

### Coverage and limitations
List `warnings` and `categories_skipped`.

### Hotspots and statistics
**Required when `violations` > 0.** Render `scan_summary.hotspots` as a table:

| Area | Count | Share | P0 | P1 | P2 |
| --- | --- | --- | --- | --- | --- |
| `area` | `count` | `share` | severity_counts… | | |

Do not invent areas. Skip this section only when hotspots is empty.

### Findings by severity
P0 / P1 / P2 tables with evidence (path, line, edge) and fix direction.
If >50 violations, show top examples per severity and state totals.

### Thematic patterns
Group repeated edges/themes with representative evidence.

### Recommended sequencing
Order `refactor_tasks` by severity/dependency.

### Checks run
`categories_scanned` minus skipped; include `owner`/`repo`/`ref`.

Details: `AUDIT_REPORT.md` in this package.
