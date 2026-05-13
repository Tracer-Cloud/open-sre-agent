Fixes #1876

#### Describe the changes you have made in this PR -

This PR makes the fresh-user quickstart path clearer and keeps provider connectivity failures on the same structured CLI error surface as missing credentials.

- Promotes `opensre onboard local_llm` in both the docs quickstart and README quick start so users can evaluate with Ollama and no API key.
- Adds a source-install fallback for older Linux systems where the binary installer may fail.
- Maps provider reachability RuntimeErrors, including Ollama connection failures and provider API timeouts, to `OpenSREError` with actionable suggestions.
- Adds CLI error-mapping coverage for Ollama unreachable and provider timeout messages.

### Demo/Screenshot for feature changes and bug fixes -

Local verification:

```text
python -m compileall app/cli/support/cli_error_mapping.py tests/cli/test_cli_error_mapping.py
cli error mapping checks passed
git diff --check
```

`pytest tests/cli/test_cli_error_mapping.py -q` could not run in this local environment:

- bundled Python has no `pytest`
- system Python is too old for the repo's Python 3.12 `type` alias syntax

---

## Code Understanding and AI Usage

**Did you use AI assistance (ChatGPT, Claude, Copilot, etc.) to write any part of this code?**
- [ ] No, I wrote all the code myself
- [x] Yes, I used AI assistance (continue below)

**If you used AI assistance:**
- [x] I have reviewed every single line of the AI-generated code
- [x] I can explain the purpose and logic of each function/component I added
- [x] I have tested edge cases and understand how the code handles them
- [x] I have modified the AI output to follow this project's coding standards and conventions

**Explain your implementation approach:**

The docs change keeps the no-key evaluation path visible before hosted-provider setup, then gives users a short source fallback when binary installation is blocked by platform compatibility.

For the CLI path, the investigation runner already funnels runtime failures through `reraise_cli_runtime_error()`. I extended that mapping for provider reachability messages from `llm_client.py`, so Ollama-not-running and API-timeout failures are rendered through the existing `OpenSREError` Rich panel rather than surfacing as raw tracebacks.

---

## Checklist before requesting a review
- [x] I have added proper PR title and linked to the issue
- [x] I have performed a self-review of my code
- [x] **I can explain the purpose of every function, class, and logic block I added**
- [x] I understand why my changes work and have tested them thoroughly
- [x] I have considered potential edge cases and how my code handles them
- [x] If it is a core feature, I have added thorough tests
- [x] My code follows the project's style guidelines and conventions

---

Note: Please check **Allow edits from maintainers** if you would like us to assist in the PR.
