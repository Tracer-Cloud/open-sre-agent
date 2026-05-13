Fixes #1874

#### Describe the changes you have made in this PR -

- Updated `PatternRule` and `RepeatRule` to match only `LogRecord.message`, not `LogRecord.raw`.
- Added regression coverage for logger-name-only false positives.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall app\hermes\rules.py tests\hermes\test_rules.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance (ChatGPT, Claude, Copilot, etc.) to write any part of this code?**
- [x] Yes, I used AI assistance (continue below)

**If you used AI assistance:**
- [x] I have reviewed every single line of the AI-generated code
- [x] I can explain the purpose and logic of each function/component I added
- [x] I have tested edge cases and understand how the code handles them
- [x] I have modified the AI output to follow this project's coding standards and conventions

**Explain your implementation approach:**

The raw log line includes structural prefixes such as timestamp, level, and logger name. Scanning it lets a logger name trigger OOM/crash-loop rules even when the message is benign. The fix keeps classification focused on parsed message text and pins both single-record and repeat-rule boundaries with tests.

---

## Checklist before requesting a review
- [x] I have added proper PR title and linked to the issue
- [x] I have performed a self-review of my code
- [x] I can explain the purpose of every function, class, and logic block I added
- [x] I understand why my changes work and have tested them thoroughly
- [x] I have considered potential edge cases and how my code handles them
- [x] If it is a core feature, I have added thorough tests
- [x] My code follows the project's style guidelines and conventions
