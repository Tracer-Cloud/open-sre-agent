Fixes #1418

#### Describe the changes you have made in this PR -

The three AWS E2E deployment helpers now resolve the actual repository root instead of stopping at `<repo>/tests`.

- Adds `tests.e2e._paths.find_repo_root()` so E2E infrastructure scripts find the root by repository markers.
- Updates Lambda, Prefect ECS, and Flink ECS deploy helpers to build shared asset paths from `tests/shared/...`.
- Updates scenario asset paths to use the real `tests/e2e/<scenario>/...` directories.
- Adds regression coverage for helper wiring and the concrete asset paths used by the deploy helpers.

### Demo/Screenshot for feature changes and bug fixes -

Local verification:

```text
python -m compileall tests/e2e/_paths.py tests/e2e/test_deploy_helper_paths.py tests/e2e/upstream_lambda/infrastructure_sdk/deploy.py tests/e2e/upstream_prefect_ecs_fargate/infrastructure_sdk/deploy.py tests/e2e/upstream_apache_flink_ecs/infrastructure_sdk/deploy.py
path checks passed
git diff --check
```

`pytest tests/e2e/test_deploy_helper_paths.py -q` could not run locally: bundled Python has no `pytest`, and system Python is too old for the repo's Python 3.12 `type` alias syntax.

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

The issue was caused by each helper using a fixed parent index from files under `tests/e2e/<scenario>/infrastructure_sdk/`, which resolves to `<repo>/tests` rather than the repo root. I added a small marker-based resolver so future directory depth changes do not recreate the same bug, then normalized the three deploy helpers around shared scenario constants.

The regression test avoids importing the deploy modules because importing them also imports cloud SDK helpers. It checks that each script is wired to the resolver, that the stale `parents[3]` pattern is gone, and that every shared/scenario asset path exists in the expected location.

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
