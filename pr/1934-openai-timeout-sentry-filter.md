Fixes #1934

#### Describe the changes you've made

Adds OpenAI-compatible provider timeout messages to the existing operator-actionable LLM Sentry filter. These errors already surface to the CLI user with clear remediation text, but they should not create high-priority Sentry issues because they usually indicate endpoint/network availability rather than an OpenSRE defect.

### Demo/Screenshot

N/A - Sentry filtering regression.

---

## Code Understanding and AI Usage

**Did you use AI assistance?**  
Yes.

## Checklist before requesting a review

- [x] I have performed a self-review of my code
- [x] I have added tests that prove my fix is effective
- [ ] `make test-cov` passes locally
- [ ] `make lint` passes locally
- [ ] `make typecheck` passes locally

Local verification:

- `C:\Users\11301\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m compileall app\utils\sentry_sdk.py tests\test_sentry_init.py`
- Pytest could not be run because neither the system Python nor bundled Python has the repo test dependencies installed, and `uv` is unavailable.
