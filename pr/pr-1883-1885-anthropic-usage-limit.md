Fixes #1883
Fixes #1885

#### Describe the changes you have made in this PR -

- Mapped Anthropic usage-limit runtime errors to `OpenSREError`.
- Added a test that verifies quota errors render as operator-actionable configuration/provider state.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall app\cli\support\cli_error_mapping.py tests\cli\test_investigate.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

The underlying provider rejection is not an OpenSRE crash. The CLI should tell the user to wait for quota reset or switch provider instead of surfacing a raw traceback.

---

## Checklist before requesting a review
- [x] Linked to both duplicate Sentry-created issues
- [x] Added regression test
- [x] Verified syntax compilation
