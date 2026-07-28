# Six-Agent Fargate MVP with Neon Credential Hydration and S3 Files

## Objective and scope

Launch the smallest credible multi-tenant remote-agent system:

- one independently controlled ECS Fargate Gateway service per organization;
- agent turns and the scheduler run inside that Gateway process;
- separate Lambdas behind HTTP APIs provide lifecycle and agent-run APIs;
- Neon stores deployment state and acts as the low-volume run queue;
- one shared Amazon S3 Files filesystem has one enforced access point per organization;
- Slack Socket Mode, Telegram polling, credentials retrieval, and Neon polling are
  outbound connections, so Gateway tasks need no stable inbound endpoint.

The MVP uses Python and boto3 rather than Terraform. It reuses an existing VPC,
explicit public subnets, ECR repositories, and Neon infrastructure. Gateway
tasks receive public IPs for outbound access; NAT is not provisioned. SQS,
DynamoDB, ALB, Service Connect, Cloud Map, task-IP registries, standalone agent
tasks, and autoscaling are deferred.

Each active organization incurs one continuously billed Fargate task. `SMALL` is the
default, and the deployment has a configurable active-organization cap. Stopping an
organization removes its compute cost but retains storage and database costs.

## Credential boundaries

| Credential | Source of truth | Runtime use |
| --- | --- | --- |
| OpenSRE integration and LLM credentials | Existing encrypted Neon credentials backend, through its credentials API | Hydrated into the tenant-mounted `~/.opensre/integrations.json` at Gateway startup |
| Tenant public API bearer credential | AWS Secrets Manager | Authenticates `POST /v1/runs` and `GET /v1/runs/{run_id}` |
| Credentials API bootstrap credential | AWS Secrets Manager | Authenticates the Gateway to the existing credentials API |
| AWS workload credentials | Tenant ECS task IAM role | S3 Files mount and explicitly permitted AWS APIs |

These credentials are never interchangeable. No secret is embedded in task-definition
environment variables, Neon control-plane rows, logs, or telemetry.

### Production Gateway credential hydration

At task startup:

1. ECS mounts the organization's S3 Files access point at `/workspace`.
2. ECS supplies `ORGANIZATION_ID`, the credentials API URL, non-secret configuration,
   and the tenant bootstrap secret ARN.
3. The Gateway retrieves that one bootstrap value at runtime through its tenant task
   role. It is not an ECS container secret because ECS resolves container secrets with
   the execution role; keeping the shared execution role unable to read tenant secrets
   preserves the isolation boundary.
   For the direct-Neon MVP worker, the secret may be a JSON runtime bundle containing
   `credentials_api_token` and `database_url`. Neither value appears in the task
   definition or logs; a raw string remains valid for hydration-only deployments.
4. Before starting Slack, Telegram, the scheduler, or the run worker, the Gateway calls
   the credentials API for its server-controlled organization.
5. The Gateway validates the response as integration-store v2.
6. It creates `/workspace/home/.opensre` with mode `0700` and atomically replaces
   `/workspace/home/.opensre/integrations.json` with mode `0600`.
7. Existing `integrations.store` loaders read the materialized local file unchanged.
8. Startup readiness fails closed when configured hydration fails.

Neon remains authoritative. The file is a runtime materialization. MVP refresh happens
only at task startup, so integration credential rotation requires an ECS task restart
or service redeployment.

### S3 Files security

- The backing bucket uses SSE-KMS and blocks public access.
- Bucket access is limited to the S3 Files service role.
- Each tenant task role may mount only its own access-point ARN.
- The access point enforces the OpenSRE UID/GID and tenant root.
- Gateway task roles receive no direct S3 object-read access for credential files.
- Disabling an organization disables both its credentials API bootstrap credential
  and public API bearer credential.

Container configuration is server controlled:

```text
HOME=/workspace/home
OPENSRE_WORKSPACE=/workspace/files
ORGANIZATION_ID=<organization>
OPENSRE_CREDENTIALS_API_URL=<real configured URL>
OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN=<tenant secret ARN>
OPENSRE_SIZE_PROFILE=SMALL|MEDIUM|LARGE
```

The mount preserves integration files, sessions, Gateway state, scheduler definitions,
and scheduler claim history.

## Test credentials on S3 Files

Live tests generate disposable non-production credentials at runtime. A temporary
Fargate setup task mounts each tenant access point and writes integration-store v2
through the filesystem, never by direct `PutObject`.

The live gate proves:

- tenants A and B load only their own file through `integrations.store`;
- the file remains mode `0600`;
- replacement tasks see the persisted file;
- tenant A cannot read or mount tenant B's access point;
- credential values appear in no task definition, control-plane row, snapshot, or log.

Test credentials point only to stubs or sandboxes and are discarded or revoked during
cleanup. LocalStack does not replace this real-AWS isolation gate. A separate unit test
covers credentials API response validation and atomic hydration.

## Authentication and routing

Lifecycle routes use API Gateway `AWS_IAM`/SigV4 authorization and are callable only by
the configured SaaS backend IAM role:

```http
PUT    /v1/organizations/{organization_id}/gateway
GET    /v1/organizations/{organization_id}/gateway
POST   /v1/organizations/{organization_id}/gateway/start
POST   /v1/organizations/{organization_id}/gateway/stop
DELETE /v1/organizations/{organization_id}/gateway
POST   /v1/organizations/{organization_id}/api-credential/rotate
```

Agent-run routes use a generated organization bearer credential stored in Secrets
Manager:

```http
POST /v1/runs
GET  /v1/runs/{run_id}
```

The bearer key identifier maps to the organization in Neon. The organization is never
accepted from request JSON. API Gateway API keys are not an authentication mechanism,
and there is no Clerk or demo authentication bypass.

No caller connects directly to a Gateway. Lambda authenticates requests and inserts
runs into Neon; each Gateway polls and claims only its organization. Slack Socket Mode
and Telegram long polling reconnect after task replacement.

## Neon model and run recovery

`tenant_deployments` records desired/actual state, size profile, AWS resource ARNs,
non-secret configuration, generic errors, and timestamps.

`tenant_api_credentials` records only key identifier, organization, Secrets Manager
ARN, enabled state, and rotation timestamps.

`agent_runs` records organization, source, optional source event ID, prompt, status,
result, generic error code, claim lease, attempts, and timestamps. Stable source IDs
are unique per organization and source.

The Gateway acquires process capacity before atomically claiming work with
`FOR UPDATE SKIP LOCKED`. Claims have renewable leases; replacement tasks may reclaim
expired work, giving at-least-once execution. Capacity is always released in `finally`.
Excess work remains `QUEUED`.

| Profile | CPU | Memory | Active turns |
| --- | ---: | ---: | ---: |
| `SMALL` | 0.5 vCPU | 1 GiB | 1 |
| `MEDIUM` | 1 vCPU | 2 GiB | 2 |
| `LARGE` | 2 vCPU | 4 GiB | 4 |

Slack, Telegram, scheduler, and API work share the same process-wide gate.

## Complete tenant lifecycle

Provisioning idempotently ensures:

1. an S3 Files access point rooted at the organization directory;
2. enforced tenant POSIX identity;
3. a tenant ECS task IAM role restricted to that access-point ARN;
4. permission to retrieve only the tenant credentials API bootstrap secret at runtime;
5. a task definition referencing only the tenant access point, tenant task role,
   shared execution role, immutable Gateway image digest, and non-secret bootstrap
   secret ARN;
6. the S3 Files volume mounted at `/workspace`;
7. the server-controlled environment described above;
8. one ECS service with `desiredCount=1`;
9. a generated tenant public API credential in Secrets Manager; and
10. deployment, run, and credential mappings in Neon.

`PUT` reconciles rather than creates duplicates. `stop` sets desired count to zero.
`delete` removes compute but preserves storage by default and disables tenant
credentials. External errors are generic; detailed exceptions remain server-side.

## Image contract

Reuse the existing ECR repository and require immutable digest references. The Gateway
image contains Python, `/bin/sh`, Bash, curl, and CA certificates. The control-plane
Lambda image also uses the existing repository.

## Six-agent delivery

Work occurs on `codex/fargate-multitenant-mvp`. Six child branches/worktrees are
integrated in two waves of three.

### Wave 1

1. **Contracts, Neon, remote credentials:** typed repository contracts, deployment/run/
   API-credential schema, credentials API client, v2 validation, and public atomic
   `replace_integrations`.
2. **AWS adapters:** existing-resource discovery, S3 Files resources/access points,
   tenant IAM, secret references, task definitions, service operations, and immutable
   digest enforcement.
3. **Images and S3 Files validation:** image dependencies and task-definition tests.

Focused tests:

```bash
uv run pytest tests/deployment/control_plane/test_api/test_models.py tests/deployment/control_plane/test_api/test_postgres_store.py tests/integrations/test_remote_credential_hydration.py tests/integrations/test_store.py
uv run pytest tests/deployment/control_plane/test_aws.py
uv run pytest tests/deployment/control_plane/test_image_contract.py tests/deployment/control_plane/test_s3files_definition.py
```

### Wave 2

4. **Tenant lifecycle:** provision/start/stop/status/delete, complete isolation
   resource reconciliation, public API credential generation/rotation/disable, and
   storage-preserving deletion.
5. **Lambda API/auth:** IAM lifecycle routes, bearer run auth, credential-to-tenant
   mapping, Neon enqueue/read, and SDK bootstrap without Clerk or demo bypasses.
6. **Gateway hydration/worker:** pre-start hydration, fail-closed readiness,
   organization-scoped polling, shared capacity, renewable leases, database-backed
   sink, clean shutdown, and replacement recovery. Reuse `GatewayTurnHandler`
   unchanged and resolve API sessions through the existing session resolver.

Focused tests (requires the private `platform/deployment_multi_tenant` submodule:
`git submodule update --init platform/deployment_multi_tenant`):

```bash
uv run pytest tests/deployment/control_plane/test_lifecycle.py
uv run pytest tests/deployment/control_plane/test_api/test_authorizer.py tests/deployment/control_plane/test_api/test_handler.py tests/deployment/control_plane/test_api/test_runtime.py
uv run pytest tests/platform/deployment_multi_tenant/test_lambda_bundle_paths.py
uv run pytest gateway/tests/runtime/test_credential_hydration.py gateway/tests/runtime/test_remote_run_worker.py gateway/tests/runtime/test_concurrency_gate.py gateway/tests/runtime/test_manager.py gateway/tests/runtime/test_turn_handler.py
```

## Integration and acceptance

Repository gates:

```bash
make lint
make format-check
make typecheck
make test-scope
make cdk-verify
make verify-integrations
uv run pytest tests/deployment/control_plane gateway/tests/runtime/test_credential_hydration.py gateway/tests/runtime/test_remote_run_worker.py
```

Real-AWS acceptance rotates any previously exposed Neon credential, provisions two
organizations, validates Neon-to-file hydration, separately validates runtime-generated
S3 Files test credentials, proves cross-tenant denial and replacement persistence,
checks rotation-on-restart, verifies public/integration credential separation, queues
work above capacity, stops one tenant without affecting the other, and deletes its
compute while preserving storage and disabling its public credential.

## Assumptions

- The existing credentials API is the production interface to encrypted organization
  credentials in Neon.
- Its real URL and bootstrap authentication are deployment configuration.
- S3 Files intentionally materializes the local OpenSRE credential store.
- Only runtime-generated sandbox credentials are used by S3 Files tests.
- Production credentials are never seeded directly by test tooling.
