# Issue #3559: Remove dead `tools/datadog_tools/` directory

## Summary
This PR removes the dead/legacy `tools/datadog_tools/` directory which only contained `__pycache__` after the Datadog tools were migrated to `integrations/datadog/`.

## Changes Made
- [x] Confirmed folder only contains `__pycache__`
- [x] Searched entire codebase for references to `tools/datadog_tools/` - **NONE FOUND**
- [x] Verified all Datadog functionality has been moved to `integrations/datadog/`
- [x] Directory is safe to delete with no breaking changes

## Verification Steps
1. **Code Search Results:**
   - Searched for "datadog_tools" across entire repo
   - Found only variable names in test files (e.g., `datadog_tools = [_registered_tool(...)]`)
   - NO imports or references to the actual `tools/datadog_tools/` directory

2. **Safety Check:**
   - No code depends on this directory
   - No imports reference this path
   - Datadog integration is fully handled by `integrations/datadog/`

## Files Affected
- `tools/datadog_tools/` - **DELETED** (empty legacy directory)

## Testing
Ready to run: `make test-cov` to verify no tests fail

## Related Issue
Closes #3559
