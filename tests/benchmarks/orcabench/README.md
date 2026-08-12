```text
orcabench/                           # OpenSRE integration for ORCA-Bench
├── README.md                           # Annotated directory map
├── __init__.py                         # Public benchmark package exports
├── config.py                           # Validated shared configuration and manifests
├── configs/                          # Checked-in experiment configurations
│   ├── native_one_task.yml             # Scored Gradient AI one-task configuration
│   └── smoke_one_task.yml               # Unscored runtime-selectable one-task configuration
├── artifacts/                        # Redacted run-artifact schema and persistence
│   ├── __init__.py                     # Public artifact exports
│   ├── models.py                       # Run, usage, status, and error models
│   ├── redaction.py                    # Recursive secret-safe serialization
│   └── writer.py                       # Deterministic atomic artifact writer
├── host/                             # Code run by Harbor on the host
│   ├── __init__.py                     # Host integration package marker
│   ├── agent.py                        # Harbor installed-agent adapter
│   ├── launcher.py                     # Exact-task Harbor batch launcher
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
    │   └── test_launcher.py            # Exact-task command tests
    └── execution/                     # Tests for in-container behavior
        ├── __init__.py                 # Execution test package marker
        ├── test_health.py               # Real HTTP health-check test
        └── test_native.py               # Native connection and report tests
```

The smoke configuration keeps experiment policy fixed while allowing the provider and
provider-native model ID to be selected at launch. Supply both options together:

```text
--config tests/benchmarks/orcabench/configs/smoke_one_task.yml \
--provider gemini \
--model gemini-3.5-flash-lite
```

The benchmark route reuses OpenSRE's provider catalog. Currently allowed routes are
`openai`, `openrouter`, `nvidia`, `gemini`, and `groq`; their existing OpenSRE
credential variables are forwarded without embedding secret values in the command.

The launcher accepts one or more exact `--task-name` values. Repeating the option
stages the snapshot once and creates one sequential Harbor job; Harbor still runs
each selected task in its own isolated trial.

```text
--task-name orca-bench/583936eecbdda829 \
--task-name orca-bench/76303b2a0ffee409
```
