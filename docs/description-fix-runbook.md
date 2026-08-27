# Description Fix Runbook

Fix tool description violations without lowering expectations.

## Policy
Improve descriptions when LLMs fail to select tools—never reduce test thresholds.

## Workflow
1. **Identify**: Find failing tools via test errors, PR feedback, or `make verify-integrations`
2. **Analyze**: Review current description; note contract violations
3. **Rewrite**: Apply contract rules (below); focus on what tool does
4. **Update**: Edit source file description only
5. **Verify**: Run failing tests, `make verify-integrations`, check help/output
6. **Track**: Remove from shrink-only allowlist if fixed; update PR/docs

## Contract Rules
- **Format**: 1 sentence, <200 chars, capitalized, ends with period
- **Content**: No placeholders (`<...>`), no credentials, no implementation details
- **Focus**: Clear action verb + specific purpose (user outcome)
- **Use Cases**: Optional 1-2 concise examples if helpful ("Useful for X and Y")
- **Skill Guidance**: Optional; only if genuinely needed for effective use

## Review Checklist
- [ ] Single sentence, <200 chars, proper caps/punct
- [ ] No placeholders, credentials, or implementation details
- [ ] Clearly states what tool does (not what it is/how it works)
- [ ] Use cases/skill guidance (if present) are relevant and concise
- [ ] Matches actual functionality; no discovery/regression (verify via tests)
- [ ] Changelog entry if required

## Verification
- **Auto**: `make lint`, `make typecheck`, tool-specific tests, `make verify-integrations`
- **Manual**: Check `opensre <tool> --help`, test invocation, confirm discovery

## Example
**Before**: `"Makes <HTTP_METHOD> requests to <URL> with optional <HEADERS> and <DATA>"`
- Issues: Placeholders, incomplete sentence, parameter-focused
**After**: `"Sends an HTTP request to a specified URL and returns the response. Useful for testing APIs and fetching web resources."`
- Valid: 1 sentence, 106 chars, no issues, tests pass

## Shrink-Only Allowlist
Remove after fixing and verifying:
1. Fix per workflow
2. Verify fix works
3. Remove from `docs/shrink-only-allowlist.md` (if listed)
4. Update PR: "Removed from shrink-only allowlist after description fix"

## Progress Tracking
Track in issue/board:
- [ ] Identify all violating tools
- [ ] Fix descriptions for [tools]
- [ ] Verify each fix
- [ ] Update allowlist as fixed
- [ ] Update docs if needed
- [ ] Final verification