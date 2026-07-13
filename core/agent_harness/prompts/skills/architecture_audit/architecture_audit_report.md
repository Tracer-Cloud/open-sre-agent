<!--
Fill this template VERBATIM as the final no-tool reply.
Rules:
- Keep every heading exactly as written (including ### / ####).
- Do not add, rename, reorder, or omit sections.
- Replace every {placeholder} with concrete content from heuristic passes
  3–6 (import, placement, size, shim), grounded in the earlier AGENT SCAN
  (layout, glossary/ADRs, early friction). Do not add a separate agent-scan
  section.
- Do NOT wrap filled values in backticks — plain text only (backticks render
  as light-blue inline code in the interactive shell).
- Escape underscores in file paths so Markdown does not restyle them:
  write \_\_init\_\_.py (not __init__.py). Unescaped __init__ becomes bold
  "init" and makes that list item look washed out / grey next to plain items.
- Use "- none" when a non-table list has no items (never drop the subsection).
- For Findings by severity, use the Markdown table exactly; if empty, one
  body row: none | — | —.
- Keep bullets/rows short; do not paste huge raw tool dumps or full bash stdout.
- Coverage and limitations should briefly note AGENT SCAN orientation
  (≤3 probes, roots discovered, glossary/ADR gaps).
-->

### Repository summary
- **Owner/repo/ref:** {owner}/{repo}
- **What it does:** {1–3 sentence summary of the repository}
- **Import findings:** {N} (contract source: {docs / build files you used})
- **Placement findings:** {N} (contract source: {build files / AGENTS-style docs you used})
- **Oversized files:** {N} (size definition: {threshold or rule you chose})
- **Compatibility shims:** {N}

### Coverage and limitations
- {scan gaps, heuristic limits, agent-scan orientation notes, or "none"}

### Hotspots and statistics
- {top areas where architecture violations are located "- none"}

### Findings by severity
| Severity | Path | Finding |
| --- | --- | --- |
| {P0 / P1 / P2} | {path} | {brief finding} |

### Thematic patterns
- {repeated edge/theme groups} or - none

### Recommended sequencing
1. {highest-priority refactor / follow-up}
2. {next}
3. {next or "none"}
