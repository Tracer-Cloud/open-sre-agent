Fixes #1469

#### Describe the changes you've made

Adds Sentry reporting to integration verification adapters when a verifier catches and converts an exception into a user-facing `missing` or `failed` result.

The change keeps the existing verification output shape, while tagging captured failures with `surface=integration`, `integration=<service>`, `phase=<verification step>`, and either `event=vendor_failure`, `event=verify_failed`, or `event=verify_config_invalid`. Discord's optional dependency path remains non-fatal and now sets `integrations.discord.import_failed`.

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

- `C:\Users\11301\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m compileall app\integrations\_verification_adapters.py tests\integrations\test_verify.py`
- Pytest could not be run because neither the system Python nor the bundled Python has this repo's test dependencies installed, and `uv` is not available in the local shell.
