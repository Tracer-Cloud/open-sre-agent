# PR Template: Remove dead tools/datadog_tools directory

## Issue
Closes #3559

## Description
This PR removes the dead legacy `tools/datadog_tools/` directory which only contained `__pycache__` after Datadog tools were migrated to `integrations/datadog/`.

## Changes
- Removed: `tools/datadog_tools/` directory (empty legacy folder)

## Verification Checklist
- [x] No references found to `tools/datadog_tools` in codebase
- [x] All Datadog functionality is in `integrations/datadog/`
- [x] No imports or dependencies on removed directory
- [x] Safe for deletion with no breaking changes

## Type of Change
- [x] Cleanup / Hygiene
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change

## Testing
This is a simple directory removal with no code changes. Recommend running:
```bash
make test-cov
```

## Related Documentation
- Issue: #3559
- Category: Hygiene
- Complexity: Small (S)
