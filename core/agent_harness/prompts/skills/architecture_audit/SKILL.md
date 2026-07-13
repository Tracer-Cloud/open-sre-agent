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

Use four separate shell_run calls — one per pass. Do not combine passes.

HARD RULES (violating any = failed turn):
- Never end the turn with shell_run as the last tool. Shell stdout is raw
  evidence only — it is NOT the user-facing answer.
- After the SHIM shell_run, the NEXT tool MUST be architecture_cleanup_repo.
  Do not run more shell_run after shim.
- Only AFTER cleanup succeeds, emit a final message with NO tools that IS the
  filled REPORT TEMPLATE from
  `core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md`.
  That final message is what the user sees in the interactive shell — if you
  skip it, they only see raw command output.
- Fill that REPORT TEMPLATE exactly: keep every heading, order, and subsection;
  do not invent alternate report layouts. Use `- none` for empty lists.
- Leaving .temp/opensre/architecture_workspace on disk is a failure.
- You write each bash command — do not copy a canned script.
- Cap each shell_run stdout hard: prefer `head` / ranked top-N (about 15–25
  lines). Huge dumps burn the turn budget and prevent the report from showing.
- Budget: clone + 4 shell passes + cleanup + final report. Do not add extra
  tool calls.

Compact sequence:
1) architecture_clone_repo(owner, repo, ref?)
   → workspace_root = .temp/opensre/architecture_workspace
   (If already in the target checkout and no owner/repo named, skip clone and
   use cwd as workspace_root; then skip cleanup.)

2) shell_run — IMPORT pass
   Discover the repo's stated layer/module import contract from layout + docs
   (AGENTS.md, ARCHITECTURE.md, CONTRIBUTING.md, build files, package maps),
   then gather evidence of cross-boundary imports that contradict that
   contract. Prefer the target repo's own rules. Treat composition roots / intentional wiring as allowed when
   the docs imply it; do not invent a stricter graph. Cap rows.

3) shell_run — PLACEMENT pass
   Discover the repo's package/module placement contract from top-level layout,
   build/module definition files (settings.gradle, go.mod, Cargo.toml,
   pyproject.toml, package.json workspaces, Bazel/Pants/Nx, etc.), and
   AGENTS-style docs. Report only placements that contradict those contracts,
   with paths + the rule they break. Cap rows.

4) shell_run — SIZE pass 
   Decide what "large" means for this repo/request. Prefer a threshold the
   user named; otherwise choose a sensible bar from context (top outliers,
   percentile, or a justified line-count cutoff). State the chosen definition
   in the report. Scan source files of ANY language (e.g. .py, .go, .ts,
   .tsx, .js, .jsx, .java, .rs, .rb, .php, .cs, .kt, .swift, .c, .cc, .cpp,
   .h, .hpp, .scala, .sh) — do NOT limit to Python and do NOT skip non-Python
   sources. Prefer primary source roots; skip only noise dirs: tests, docs,
   examples, caches, .venv, node_modules, dist, build, vendor lock dirs, and
   binary/media assets (images, fonts, lockfiles, generated minified bundles).
   Cap rows.

5) shell_run — SHIM pass
   Lightweight heuristic for compatibility / re-export / facade modules across
   languages. Distinguish deliberate public API entrypoints (keep as evidence,
   do not treat as debt by default) from thin leftover forwarding modules.
   Keep output short (cap rows).

6) architecture_cleanup_repo()  ← required next tool after step 5

7) Final NO-TOOL reply: fill
   `core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md`
   from the four shell passes. Summarize; never paste huge raw dumps.
   Invent Recommended sequencing yourself — calibrate to the repo's stated
   contract, not a generic "delete every cross-module edge" story. Propose
   tasks; never auto-apply fixes. File GitHub issues only after approval.
