# CI Path Filter Rules

This repo uses `dorny/paths-filter` to skip expensive jobs when
only non-code files change.

## Change groups

| Group       | Paths watched                          | Gates jobs          |
|-------------|----------------------------------------|---------------------|
| `python`    | `app/**`, `pyproject.toml`, config     | typecheck, lint     |
| `tests`     | `tests/**`, `app/**`                   | test                |
| `workflows` | `.github/workflows/**`                 | all jobs (safety)   |
| `docs`      | `docs/**`, `**.md`                     | nothing (skip all)  |

## Example scenarios

- Docs-only PR (`README.md` edited): all expensive jobs skipped ✅
- New test added (`tests/test_foo.py`): `test` job runs, typecheck skips ✅
- App code changed (`app/nodes/`): all jobs run ✅
- Nightly scheduled run: all jobs always run (no filtering) ✅