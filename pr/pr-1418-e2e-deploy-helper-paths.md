Fixes #1418

#### Describe the changes you have made in this PR -

- Corrected three E2E deploy helpers to resolve the repository root with `parents[4]`.
- Fixed scenario asset paths to include `tests/e2e/...` where needed.
- Added regression tests that assert shared and scenario assets resolve to existing paths.

### Demo/Screenshot for feature changes and bug fixes -

```bash
python -m compileall tests\e2e\test_deploy_helper_paths.py tests\e2e\upstream_lambda\infrastructure_sdk\deploy.py tests\e2e\upstream_prefect_ecs_fargate\infrastructure_sdk\deploy.py tests\e2e\upstream_apache_flink_ecs\infrastructure_sdk\deploy.py
```

---

## Code Understanding and AI Usage

**Did you use AI assistance?**
- [x] Yes, reviewed line by line

**Explain your implementation approach:**

Files under `tests/e2e/<scenario>/infrastructure_sdk/deploy.py` need four parent hops to reach the repo root. Once that base is correct, shared assets stay under `tests/shared`, while scenario assets are under `tests/e2e/<scenario>`.

---

## Checklist before requesting a review
- [x] Linked to issue
- [x] Added regression tests
- [x] Verified syntax compilation
