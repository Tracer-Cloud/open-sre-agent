# Add incident.io Integration

This plan details the addition of a first-class `incident.io` integration to OpenSRE. The integration will allow the agent to read incident context and metadata, and post timeline events back to incidents.

## User Review Required

> [!IMPORTANT]
> - Do you prefer a single tool (`IncidentIoTool`) that handles both searching incidents and adding timeline events, or separate tools (e.g., `IncidentIoListTool` and `IncidentIoTimelineTool`)? I will proceed with a single multi-purpose tool or two focused tools based on your preference. (I'm planning on using a single `IncidentIoIncidentsTool` that has options to read incidents and add timeline events).
> - End-to-end coverage: I will create unit and synthetic/e2e tests that mock the incident.io API using standard OpenSRE testing patterns. A screen video demo of the setup and investigation flow will need to be provided in the PR description, which is standard for new integrations.

## Proposed Changes

### Configuration & Models

#### [MODIFY] app/integrations/config_models.py
Add `IncidentIoIntegrationConfig` model:
- `api_key: str`
- `base_url: str = "https://api.incident.io"`
- `integration_id: str = ""`

### Service Client

#### [NEW] app/services/incident_io/client.py
Implement `IncidentIoClient` using `httpx.Client`. It will support:
- `probe_access()`: Validates API key by hitting a lightweight endpoint (e.g. `/v2/incidents` with limit=1).
- `list_incidents(query)`: Retrieves open incidents.
- `get_incident(incident_id)`: Gets details of a specific incident.
- `add_timeline_event(incident_id, description)`: Posts an update/timeline event to an incident.

#### [NEW] app/services/incident_io/__init__.py
Export `IncidentIoClient`, `IncidentIoConfig`, and a `make_incident_io_client` factory function.

### Verification & Registry

#### [MODIFY] app/integrations/_verification_adapters.py
- Add `_verify_incident_io = build_probe_verifier("incident_io", build_config=IncidentIoIntegrationConfig.model_validate, client_factory=IncidentIoClient)`
- Add to `__all__`.

#### [MODIFY] app/integrations/registry.py
- Add `IntegrationSpec(service="incident_io", verifier=_verify_incident_io, direct_effective=True, verify_order=34)` to `INTEGRATION_SPECS`.

#### [MODIFY] app/integrations/verify.py
- Add `_verify_incident_io` to the imports and `__all__`.

### Tool Implementation

#### [NEW] app/tools/IncidentIoIncidentsTool/__init__.py
Implement an `IncidentIoIncidentsTool` (subclassing `BaseTool`) that requires `api_key`.
- `name = "incident_io_incidents"`
- `source = "incident_io"`
- Allow inputs for listing incidents (status, query) or adding a timeline event (incident_id, comment).

### Documentation

#### [NEW] docs/incident_io.mdx
Add a documentation page covering how to configure the integration, generate an API key in incident.io, and verify connectivity.

### Tests

#### [NEW] tests/integrations/test_incident_io.py
Add tests covering config normalization, client initialization, and probe verification.

#### [NEW] tests/tools/test_incident_io_tool.py
Add tests for the `IncidentIoIncidentsTool`, mocking the `IncidentIoClient`.

## Verification Plan

### Automated Tests
- `make typecheck` and `make lint` to ensure strict typing and code quality.
- `make test-cov` to run unit tests and ensure functionality.
- `make verify-integrations` to test the registry and local verification loop.

### Manual Verification
- We will build the integration locally and set a local `INCIDENT_IO_API_KEY` (or add via CLI) and test `opensre integrations verify incident_io`.
- Verify the tool can be invoked by the agent to fetch incidents and post updates in an investigation flow.
