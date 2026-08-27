# Description Fix Runbook

This runbook provides a standardized process for fixing tool description violations that cause tool selection failures. The goal is to improve descriptions to meet the contract without lowering expectations for tool capabilities.

## Policy

When a tool fails to be selected by the LLM due to description issues, we fix the description itself—not by reducing the tool's expected capabilities or use cases. The description must accurately and completely represent what the tool does while following the description contract.

## Step-by-Step Workflow

### 1. Identify the Violation
- Look for test failures or error messages indicating "description contract violation"
- Check PR comments or code review feedback about description issues
- Run `make verify-integrations` or relevant tests to find failing tools

### 2. Analyze the Current Description
- Read the tool's current description in its source file
- Identify which part(s) violate the contract (see contract rules below)
- Determine what information is missing or incorrect

### 3. Rewrite the Description
- Follow the description contract rules strictly
- Ensure the description is:
  - One clear sentence
  - Properly capitalized and punctuated
  - Free of placeholders like `<...>`
  - Free of credentials or implementation details
  - Focused on what the tool does, not how
- Include relevant use cases if they help clarify the tool's purpose
- Add skill guidance if the tool requires specific skills to be effective

### 4. Update the Tool
- Edit the tool's source file to replace the description
- Ensure no other contract violations are introduced
- Keep the change focused only on the description fix

### 5. Verify the Fix
- Run the specific tests that were failing
- Run `make verify-integrations` to ensure no regressions
- Check that the tool still appears in discovery and can be selected appropriately
- Confirm the description renders correctly in help text and documentation

### 6. Update Tracking
- If the tool was on the shrink-only allowlist, consider removing it if the fix resolves the issue
- Update any PR progress tracking documents or issues
- Add a note to the PR description about the description fix

## Description Contract Rules

### Length and Format
- Must be a single sentence (ending with a period)
- Maximum 200 characters (including spaces and punctuation)
- Start with a capital letter, end with a period
- No trailing whitespace

### Content Requirements
- **No placeholders**: Do not include `<tool_name>`, `<parameter>`, `<value>`, or similar template syntax
- **No credentials**: Never mention API keys, tokens, passwords, or authentication details
- **No implementation details**: Avoid phrases like "uses curl internally", "calls the X API", "built with Y library"
- **Clear action verb**: Begin with what the tool does (e.g., "Fetches", "Converts", "Executes", "Renders")
- **Specific purpose**: Describe the exact function, not a general category
- **User-focused**: Explain what the user accomplishes, not internal mechanics

### Use Cases (Optional but Recommended)
- If the tool has common usage patterns, include 1-2 concise examples
- Format: "Useful for [specific task] and [another task]."
- Keep use cases brief and directly related to the tool's core function

### Skill Guidance (When Applicable)
- If the tool requires specific skills for effective use, mention them
- Format: "Requires [skill name] for [purpose]."
- Only include skills that are genuinely necessary, not just helpful

## Review Checklist for PR Approvers

- [ ] Description is a single sentence with proper capitalization and punctuation
- [ ] Description is under 200 characters
- [ ] No placeholders (`<...>`) present
- [ ] No credentials or authentication details mentioned
- [ ] No implementation details about how the tool works internally
- [ ] Description clearly states what the tool does (not what it is or how it works)
- [ ] Use cases (if included) are relevant and concise
- [ ] Skill guidance (if included) is accurate and necessary
- [ ] Description matches the tool's actual functionality
- [ ] No regression in tool discovery or selection (verified by running relevant tests)
- [ ] Changelog entry added if required by project policy

## Verification Steps

### Automated Checks
1. Run `make lint` to catch formatting issues
2. Run `make typecheck` to ensure no type errors
3. Run tests specific to the tool: `python -m pytest tests/<tool_test_path> -v`
4. Run integration verification: `make verify-integrations`

### Manual Verification
1. Check the tool's help output: `uv run opensre <tool> --help`
2. Verify the description appears correctly in the help text
3. Test the tool with a simple invocation to ensure it still works
4. Confirm the tool appears in `opensre --help` or equivalent discovery mechanism

## Walkthrough Example

**Problem**: The `http_request` tool description was: "Makes <HTTP_METHOD> requests to <URL> with optional <HEADERS> and <DATA>"

**Violations**:
- Contains placeholders (`<HTTP_METHOD>`, `<URL>`, `<HEADERS>`, `<DATA>`)
- Not a complete sentence (missing period)
- Focuses on parameters rather than purpose

**Fix Process**:
1. Identified the placeholders and incomplete sentence
2. Determined the tool's actual purpose: sending HTTP requests and returning responses
3. Rewrote to: "Sends an HTTP request to a specified URL and returns the response."
4. Added use case: "Useful for testing APIs and fetching web resources."
5. Final description: "Sends an HTTP request to a specified URL and returns the response. Useful for testing APIs and fetching web resources."

**Verification**:
- Description is now one sentence: "Sends an HTTP request to a specified URL and returns the response. Useful for testing APIs and fetching web resources."
- Under 200 characters: 106 characters
- No placeholders, credentials, or implementation details
- Clear action verb ("Sends")
- Tests pass and tool still appears in discovery

## Shrink-Only Allowlist Guidance

The shrink-only allowlist contains tools that are permitted to have non-compliant descriptions temporarily while fixes are being worked on.

### When to Remove from Allowlist
- After successfully fixing the description according to this runbook
- When verification confirms the tool works correctly with the new description
- After updating any related documentation

### When to Keep on Allowlist
- If the description fix requires significant tool changes beyond the description
- If additional investigation is needed to understand the tool's true purpose
- When waiting for upstream changes in an integrated service

### Process for Removal
1. Fix the description following this runbook
2. Verify the fix works
3. Remove the tool from `docs/shrink-only-allowlist.md` (if listed)
4. Update the PR to reflect the removal
5. Add a note: "Removed from shrink-only allowlist after description fix"

## PR Progress Tracking

For large description fix efforts, track progress in the issue or project board:

- [ ] Identify all tools with description violations
- [ ] Fix descriptions for [tool A], [tool B], [tool C]
- [ ] Verify each fix with tests and manual checks
- [ ] Update shrink-only allowlist as tools are fixed
- [ ] Update documentation if needed
- [ ] Final verification run

Update the tracking as each tool is completed to maintain visibility into overall progress.