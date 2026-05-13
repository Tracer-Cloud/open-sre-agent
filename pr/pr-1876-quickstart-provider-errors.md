Fixes #1876

#### Describe the changes you have made in this PR -

- Promoted `opensre onboard local_llm` in the quickstart and README.
- Added a source-install fallback for older Linux hosts where the binary may not run.
- Mapped Ollama connectivity failures to `OpenSREError` so CLI users get a clean actionable message instead of a traceback.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall app\cli\support\cli_error_mapping.py tests\cli\test_investigate.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

The docs change makes the no-key local path visible before hosted-provider onboarding. The runtime change reuses the existing CLI error mapping boundary so provider connectivity errors render like other setup issues.

---

## Checklist before requesting a review
- [x] Linked to issue
- [x] Docs updated with behavior change
- [x] Added unit coverage for the error mapping
