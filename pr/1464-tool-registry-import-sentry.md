Fixes #1464

#### Describe the changes you've made

Reports tool-registry import failures through the shared Sentry error helper instead of only logging warnings. Internal `app.*` missing modules and non-import exceptions are tagged as registry bugs, while external optional dependency misses remain warning-level events.

The registry now also sets a best-effort `tools.import_failures` Sentry tag after loading, so process events can show how many tool modules failed to import.

### Demo/Screenshot

N/A - telemetry and regression-test change.

---

## Code Understanding and AI Usage

**Did you use AI assistance?**  
Yes.

## Checklist before requesting a review

- [x] I have performed a self-review of my code
- [x] I have added tests that prove my fix is effective or that my feature works
- [ ] `make test-cov` passes locally
- [ ] `make lint` passes locally
- [ ] `make typecheck` passes locally

Local verification:

- `python -m compileall app/tools/registry.py tests/tools/test_registry.py`
- `python -m pytest tests/tools/test_registry.py -q` could not run in this local Python because the repo uses newer Python syntax in `app/analytics/provider.py` (`type PropertyValue = ...`).
