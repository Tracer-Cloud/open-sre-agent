Fixes #1850

#### Describe the changes you have made in this PR -

- Added an explicit Unix-domain socket support guard in `app.agents.bus`.
- Made liveness checks return false on platforms without `socket.AF_UNIX`.
- Skipped Unix-socket bus tests on platforms that cannot run them.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall app\agents\bus.py tests\agents\test_bus.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

This is the conservative compatibility fix: Windows no longer crashes with `AttributeError` from `socket.AF_UNIX`; callers receive a clear unsupported-platform error. A future PR can add a TCP transport if maintainers choose that direction.

---

## Checklist before requesting a review
- [x] Linked to issue
- [x] Added platform guard
- [x] Kept scope narrow while maintainers discuss full transport abstraction
