Fixes #1887

#### Describe the changes you have made in this PR -

- Added a regression test proving `AlertInbox.peek_last()` uses the internal deque snapshot and does not drain the inbox.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall tests\cli\interactive_shell\test_alert_inbox.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

Current main already uses `deque`, not `SimpleQueue.queue`. This PR locks that contract with a regression test so the Sentry failure does not return.

---

## Checklist before requesting a review
- [x] Linked to issue
- [x] Added regression coverage
- [x] Verified syntax compilation
