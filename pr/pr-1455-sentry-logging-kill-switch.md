Fixes #1455

#### Describe the changes you have made in this PR -

- Added `OPENSRE_SENTRY_LOGGING_DISABLED=1` support.
- Kept direct `capture_exception` behavior enabled when only logger auto-capture is disabled.
- Documented the new env var in the telemetry kill-switch matrix.
- Added a unit test for the integration builder.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall app\utils\sentry_sdk.py tests\test_sentry_init.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

`LoggingIntegration` was already wired. This completes the requested opt-out switch so noisy deployments can disable logger-derived Sentry events without disabling explicit exception capture.

---

## Checklist before requesting a review
- [x] Linked to issue
- [x] Added test
- [x] Updated docs
