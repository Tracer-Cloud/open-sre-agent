Fixes #1904

#### Describe the changes you have made in this PR -

- Moved assistant spinner startup until after routing decides the turn is not a slash command.
- Added `begin_assistant_spinner()` on the streaming console bridge.
- Added tests proving slash commands do not start the assistant/token spinner, while LLM routes still do.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall app\cli\interactive_shell\loop.py tests\cli\interactive_shell\test_loop.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

The spinner was started before routing, so menu slash commands inherited the assistant-token UI despite not calling an LLM. The spinner now starts only for non-slash routes after `route_input` returns.

---

## Checklist before requesting a review
- [x] Linked to issue
- [x] Added regression tests
- [x] Verified syntax compilation
