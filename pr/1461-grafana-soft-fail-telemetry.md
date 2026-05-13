Fixes #1461

#### Describe the changes you've made

Adds a shared Grafana service-client telemetry helper and wires it into the soft-fail paths in `base.py`, `loki.py`, `mimir.py`, and `tempo.py`.

The existing fallback return values are preserved, but datasource discovery, label values, alert rules, Loki queries, Mimir queries, Tempo queries, and Tempo trace-detail non-200 responses now report a tagged Sentry event.

### Demo/Screenshot

N/A - telemetry and regression-test change.

---

## Code Understanding and AI Usage

**Did you use AI assistance?**  
Yes.

## Checklist before requesting a review

- [x] I have performed a self-review of my code
- [x] I have added tests that prove my fix is effective or that my feature works
- [ ] `make test-cov` passes locally
- [ ] `make lint` passes locally
- [ ] `make typecheck` passes locally

Local verification:

- `C:\Users\11301\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m compileall app\services\grafana tests\services\test_grafana_loki.py tests\services\test_grafana_mimir.py tests\services\test_grafana_tempo.py`
- Pytest could not be run because neither the system Python nor bundled Python has the repo test dependencies installed, and `uv` is unavailable.
