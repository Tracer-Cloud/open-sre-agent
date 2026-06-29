# Gateway Package Guidance

Gateway tests live in `gateway/tests/`, not the repo-wide `tests/` tree.

This package is a bounded messaging surface with its own app entrypoint,
platform adapters, storage, security, sinks, and process runner. Keeping its
tests package-local makes gateway refactors easier to review and keeps the
gateway implementation and regressions together. New gateway unit tests should
be added under `gateway/tests/`.

Pytest discovers these tests through `pytest.ini`; scoped CI maps changes under
`gateway/` to `gateway/tests/` through `infra/ci/test_scope_rules.py`.