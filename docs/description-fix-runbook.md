# Description Fix Runbook

Fix tool description violations to ensure the model can properly select tools.
Rewriting descriptions is the only allowed fix—never lower expectations by changing test thresholds.

## Description Contract Rules

Each tool's description must satisfy all:

1. **Length**: ≥20 non-empty characters
2. **No placeholders**: Doesn't start with TODO/TBD/FIXME/"description here"/placeholder/lorem ipsum
3. **No credentials**: Free of API keys, tokens, private keys, or other secret patterns
4. **Use cases for siblings**: If the tool shares its `source` with other tools, `use_cases` must be non-empty and meaningful
5. **Skill guidance limit**: `skill_guidance` < 2,400 characters

Violations are tracked in a shrink-only allowlist in `tests/tools/test_description_contract.py`.

## Fix Workflow

Follow these exact steps for each violation:

### 1. Identify the violation
```bash
uv run python -m pytest tests/tools/test_description_contract.py -q -k description_contract
```
Record the failing tool name and specific violation reason(s) from the test output.

### 2. Examine the current implementation
Locate the tool's source file:
- `tools/<tool_name>.py` or `tools/<category>/<tool_name>.py`
- `integrations/<vendor>/tools/<tool_name>.py`

Check these fields:
- `description` (primary focus)
- `use_cases` (if tool has siblings per test output)
- `skill_guidance` (if flagged as too long)

### 3. Apply the appropriate fix per violation type

**Too short description** (< 20 chars):
- Expand to clearly state: what the tool does, its primary purpose, and any key constraints or behaviors
- Example: Change `"Fetch data"` → `"Retrieves metric data from the specified monitoring endpoint with optional time-range filtering"`

**Placeholder text** (starts with TODO/TBD/etc.):
- Replace entirely with an accurate, functional description
- Never keep or modify placeholder text—write a new description from scratch

**Contains credentials/secrets**:
- Remove all credential patterns completely
- If examples are essential, use obviously fake values like `"example-api-key-123"` or redact with `"[REDACTED]"`

**Missing use_cases** (when tool has siblings):
- Add 2-3 specific, concrete use cases that differentiate this tool from others sharing the same source
- Focus on when to choose THIS tool over its siblings (user goals/scenarios, not technical features)
- Good: ["Audit historical configuration changes", "Troubleshoot drifting infrastructure state", "Compliance reporting on resource configurations"]
- Avoid: ["Makes API calls", "Handles pagination", "Returns JSON"]

**Excessive skill_guidance** (≥ 2,400 chars):
- Trim to agent-relevant essentials: when to use the tool, key behaviors, and important limitations
- Move detailed examples, background info, or lengthy explanations to external documentation
- Preserve any critical agent decision-making guidance

### 4. Verify the fix
- Run the same test command from Step 1 to confirm the tool now passes
- Run the tool's specific tests (if any) to ensure the description change didn't accidentally modify functional code
- Consider running related integration tests if this is a vendor tool

### 5. Update the allowlist
In `tests/tools/test_description_contract.py`:
- Remove the fixed tool's name from the `_DESCRIPTION_CONTRACT_ALLOWLIST` frozenset
- **This step is mandatory**—the allowlist only shrinks; fixed tools must exit the list

### 6. Update your pull request
In the PR description, note progress: 
`Fixed descriptions for N tools; M remain in backlog`

## Finding Quality References

When fixing descriptions, consult these sources for examples of effective tool descriptions:
- Recently fixed tools: Check git history for commits removing tools from the allowlist
- High-signal tools: Look at frequently-used tools like `github_create_issue`, `slack_send_message`, `kb_search`
- Domain patterns: Observe how similar tools in the same integration/vendor describe themselves
- Contract-compliant tools: Any tool not currently in the allowlist (though verify recently)

## Example Fix: Multiple Violation Types

**Before** (failing on multiple counts):
```python
@tool("example_tool")
class ExampleTool(BaseTool):
    description = "TODO: fix this"  # Placeholder + too short (13 chars)
    use_cases = []  # Missing - tool has siblings
    skill_guidance = "A" * 2500  # Too long
```

**After** (passing all checks):
```python
@tool("example_tool")
class ExampleTool(BaseTool):
    description = "Analyzes HTTP response patterns to detect anomalies in API behavior and performance."
    use_cases = [
        "Identify unusual latency spikes in REST endpoints",
        "Detect unexpected status code distributions",
        "Find patterns in failed authentication attempts"
    ]
    skill_guidance = "Use this tool when investigating API reliability issues. Focus on response time distributions and status code patterns. Less effective for deep content analysis."
```

## Common Anti-examples to Avoid

**Vague/Phrasal descriptions** (too generic):
- ❌ "Handles data operations" 
- ✅ "Extracts, transforms, and loads data between SQL databases and S3 storage"

**Implementation leakage** (focus on how, not what/when):
- ❌ "Makes HTTP GET requests with exponential backoff and JSON parsing"
- ✅ "Retrieves and normalizes user profile data from identity providers"

**Overly technical jargon** (obscures purpose):
- ❌ "Utilizes RESTful endpoints with OAuth2 authentication"
- ✅ "Connects to secure APIs to fetch and update customer records"

**Placeholder remnants** (incomplete fixes):
- ❌ "Fetches user data from the API (TODO: add error handling)"
- ✅ "Retrieves current user profile information from the authentication service"

## Review Checklist for Description Fixes

When reviewing a PR that fixes description violations, verify:

- [ ] **Violation resolved**: The specific issue(s) noted in the test output are fully addressed
- [ ] **Description quality**: Clearly explains what the tool does and when to use it (≥20 chars, no filler)
- [ ] **No placeholders**: Zero TODO/TBD/FIXME or similar text
- [ ] **No secrets**: Absolutely no credential patterns (API keys, tokens, etc.)
- [ ] **Use cases appropriate**: If tool has siblings, `use_cases` exist and help disambiguate from similar tools
- [ ] **Skill guidance concise**: < 2,400 chars and provides meaningful context for agent tool selection
- [ ] **Technical accuracy**: Verify description matches the tool's actual implementation—run the tool's help/docstring or tests if uncertain about behavior
- [ ] **Allowlist updated**: Tool name removed from `_DESCRIPTION_CONTRACT_ALLOWLIST`
- [ ] **No test changes**: Description contract test itself is unmodified (expectations not lowered)

## Key Principles

- **Improve descriptions only**: Fixes must enhance description quality—never modify tests, thresholds, or the allowlist to accommodate poor descriptions
- **Shrink-only allowlist**: The allowlist exclusively tracks remaining work; fixed tools **must** be removed to maintain progress measurement
- **Signal over completeness**: Prioritize clear, distinctive signals that help the model choose correctly over exhaustive detail
- **Consistency with peers**: Match description style, tone, and information density of similar tools in the same domain
- **Prefer actionable guidance**: Focus descriptions on what agents need to know to make good tool selection decisions
- **Signal density**: Aim for 1-2 distinct pieces of actionable information per sentence
- **Scannability**: Front-load the most important distinguishing information
- **Agent utility**: Ask 'Would this help an LLM decide when to reach for this tool vs. alternatives?'

## Common Pitfalls to Avoid

**Over-correcting length**: Don't add filler just to reach 20 chars—every word should add signal value.
**Incorrect use_cases**: Don't duplicate the description in use_cases; they should complement each other.
**Ignoring sibling context**: Always check what other tools share the same source to understand needed differentiation.
**Preserving accidental secrets**: Double-check for hidden credentials in comments or example values that might trigger the secret regex.
**Allowlist mismatches**: Ensure you're editing the correct allowlist line—there's only one `_DESCRIPTION_CONTRACT_ALLOWLIST` in the file.

## Related Files

- **Contract enforcement**: `tests/tools/test_description_contract.py` (test & allowlist)
- **Tool implementations**: 
  - `tools/` (cross-cutting and system tools)
  - `integrations/<vendor>/tools/` (vendor-specific tools)
- **Authoring reference**: `docs/adding-tools-and-integrations.md` (guidance for new tool descriptions)