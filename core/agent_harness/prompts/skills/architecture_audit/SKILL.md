══════════════════════════════════════════════════════════
ARCHITECTURE AUDIT SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE (call this skill when the user ask matches any of these):
- Architecture audit / architecture review / architecture violations
- Layer / import / module-placement health of a repo
- Oversized source files or compatibility-shim hunts (any language)
- Structural summary of a codebase (what it does, hotspots, debt themes,
  recommended refactor sequencing) — not a one-line README paraphrase
- Phrases like: "audit Tracer-Cloud/opensre", "find architecture issues",
  "summarize this repo's architecture", "what's wrong with the layering",
  "find huge files / shims", "architecture report for owner/repo"

Do NOT use this skill for: live incident RCA, metric/log queries, deploying,
or ordinary chat that only needs a short verbal overview with no scan.

Use shell_run for size + shim heuristics.

HARD RULES (violating any = failed turn):
- Never end the turn with shell_run as the last tool. Shell stdout is raw
  evidence only — it is NOT the user-facing answer.
- After clone + scans + bash, the NEXT tool MUST be architecture_cleanup_repo.
- Only AFTER cleanup succeeds, emit a final message with NO tools that IS the
  filled REPORT TEMPLATE from
  `core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md`.
  That final message is what the user sees in the interactive shell — if you
  skip it, they only see raw command output.
- Fill that REPORT TEMPLATE exactly: keep every heading, order, and subsection;
  do not invent alternate report layouts. Use `- none` for empty lists.
- Leaving .temp/opensre/architecture_workspace on disk is a failure.

Compact sequence (prefer parallel tools where independent):
1) architecture_clone_repo(owner, repo, ref?)
   → workspace_root = .temp/opensre/architecture_workspace
   (If already in the target checkout and no owner/repo named, skip clone and
   use cwd as workspace_root; then skip cleanup.)
2) In one step if possible, call BOTH:
   - scan_architecture_imports(workspace_root=..., owner=..., repo=...)
   - scan_module_placement(workspace_root=..., owner=..., repo=...)
3) Invent and run ONE shell_run that covers size + shim heuristics under the
   workspace (or cwd). You write the bash — do not copy a canned command.
   Goals for that command:
   - Size pass: decide what "large" means for this repo/request. Prefer a
     threshold the user named; otherwise choose a sensible bar from context
     (e.g. top outliers, percentile, or a line-count cutoff you justify).
     State the chosen definition in the report. Scan source files of ANY
     language (e.g. .py, .go, .ts, .tsx, .js, .jsx, .java, .rs, .rb, .php,
     .cs, .kt, .swift, .c, .cc, .cpp, .h, .hpp, .scala, .sh) — do NOT limit
     to Python and do NOT skip non-Python sources. Prefer the repo's primary
     source roots; skip only noise dirs: tests, docs, examples, caches,
     .venv, node_modules, dist, build, vendor lock dirs, and binary/media
     assets (images, fonts, lockfiles, generated minified bundles).
   - Shim pass: lightweight heuristic for compatibility / re-export shims
     across languages (keywords and/or thin re-export / facade modules).
     Keep output short (cap rows).
4) architecture_cleanup_repo()  ← required next tool after step 3
5) Final NO-TOOL reply: fill
   `core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md`
   from the output of steps 2 and 3. Summarize; never paste huge raw dumps.
   Propose tasks; never auto-apply fixes. File GitHub issues only after approval.
