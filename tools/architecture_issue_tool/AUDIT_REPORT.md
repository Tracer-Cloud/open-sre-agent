# Architecture audit report outline

Use this outline when writing the user-facing Markdown report after
`find_architecture_violations` returns JSON. Synthesize from tool data only; do not
invent findings.

## Report sections

### Executive summary

- 2–4 sentences on overall health, top risks, and recommended next step.
- Anchor counts with `scan_summary.severity_counts`, `scan_summary.kind_counts`, and
  `scan_summary.violations`.
- If `scan_summary.coverage_complete` is false, state that coverage was partial.

### Coverage and limitations

- List `scan_summary.warnings` and `scan_summary.categories_skipped`.
- Explain which checks did not run and what that means for confidence.
- Import/layer checks use polyglot tree-sitter extraction; oversized/shim/placement
  checks are Python/OpenSRE-specific when those categories are selected.
- Never call the repo "clean" when `coverage_complete` is false.

### Findings by severity

Use tables per severity band. Every row must cite `evidence` (path, line, edge, or
rule).

#### P0 — CI-breaking or active layer violation

| ID | Issue | Evidence | Fix direction |
| --- | --- | --- | --- |
| ... | ... | ... | ... |

#### P1 — Structural debt

| ID | Issue | Evidence | Fix direction |
| --- | --- | --- | --- |
| ... | ... | ... | ... |

#### P2 — Maintainability and placement

| ID | Issue | Evidence | Fix direction |
| --- | --- | --- | --- |
| ... | ... | ... | ... |

### Thematic patterns

- Group related violations (for example repeated `core -> integrations` edges).
- Name the architectural theme and list representative examples with evidence.
- When `violations` length exceeds 50, summarize themes and show only top examples
  per severity; always state total counts.

### Recommended sequencing

- Derive from `refactor_tasks` (atomic scope), not a mega-refactor.
- Order by severity and dependency (P0 layer fixes before placement cleanups).

### Checks run

- List categories from `scan_summary.categories_scanned` minus
  `scan_summary.categories_skipped`.
- Note repo `owner`, `repo`, and `ref` from the tool result.

## Rules

1. Evidence over opinion — cite paths, lines, or import edges from tool JSON.
2. Do not paste the full raw JSON or every violation when counts are large.
3. Do not claim zero issues when skipped categories or warnings indicate incomplete
   coverage.
4. Do not auto-apply fixes; propose tasks only.
5. Severity guide: P0 = layer/CI boundary breaks; P1 = shims and structural debt;
   P2 = oversized files and placement heuristics.
