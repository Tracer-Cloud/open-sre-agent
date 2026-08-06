```text
orcabench/                           # One-task OpenSRE integration for ORCA-Bench
├── README.md                           # Annotated directory map
├── CONTEXT.md                          # Setup, operation, artifacts, and limitations
├── plan.md                             # Scope, architecture, and acceptance criteria
├── __init__.py                         # Public benchmark package exports
├── config.py                           # Validated shared configuration and manifests
├── configs/                          # Checked-in experiment configurations
│   ├── native_one_task.yml             # Scored Gradient AI one-task configuration
│   └── openrouter_smoke_one_task.yml   # Unscored OpenRouter one-task smoke configuration
├── artifacts/                        # Redacted run-artifact schema and persistence
│   ├── __init__.py                     # Public artifact exports
│   ├── models.py                       # Run, usage, status, and error models
│   ├── redaction.py                    # Recursive secret-safe serialization
│   └── writer.py                       # Deterministic atomic artifact writer
├── host/                             # Code run by Harbor on the host
│   ├── __init__.py                     # Host integration package marker
│   ├── agent.py                        # Harbor installed-agent adapter
│   ├── launcher.py                     # Exact one-task Harbor launcher
│   ├── pricing.py                      # ORCA pricing adapter
│   ├── snapshot.py                     # Docker snapshot cache staging
│   ├── validation.py                   # Fast prerequisite validation CLI
│   └── bundle/                        # Offline OpenSRE installation bundle
│       ├── __init__.py                 # Public bundle exports
│       ├── build.py                    # Reproducible wheelhouse builder CLI
│       └── manifest.py                 # Bundle integrity and path validation
├── execution/                        # Code installed and run inside the ORCA task
│   ├── __init__.py                     # In-container execution package marker
│   ├── contracts.py                    # Mode variation-point protocols
│   ├── environment.py                  # Readiness and OpenSRE environment setup
│   ├── health.py                       # Real Grafana readiness check
│   ├── modes.py                        # Execution-mode composition
│   ├── native_connection.py            # Connection-only Grafana bridge
│   ├── native_investigation.py         # Native OpenSRE investigation lifecycle
│   ├── native_report.py                # Exact native report persistence
│   └── runner.py                       # In-container composition root
└── tests/                            # Focused benchmark integration tests
    ├── __init__.py                     # Test package marker
    ├── _support.py                     # Shared real test-data builders
    ├── test_artifacts.py               # Artifact persistence and redaction tests
    ├── test_config.py                  # Strict configuration tests
    ├── host/                          # Tests for host-side behavior
    │   ├── __init__.py                 # Host test package marker
    │   ├── test_agent.py               # Real Harbor adapter contract tests
    │   ├── test_bundle.py              # Bundle integrity and safety tests
    │   └── test_launcher.py            # Exact one-task command tests
    └── execution/                     # Tests for in-container behavior
        ├── __init__.py                 # Execution test package marker
        ├── test_health.py               # Real HTTP health-check test
        └── test_native.py               # Native connection and report tests
```
