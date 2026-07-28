# `platform/deployment_fargate/`

Multi-tenant Fargate deployment for OpenSRE: control-plane Lambda, public API
forwarder, and shared fleet CDK.

EC2 AWS SDK primitives and the Telegram gateway AMI/systemd lifecycle live in
[`../deployment_ec2/`](../deployment_ec2/)
([`telegram_gateway/`](../deployment_ec2/telegram_gateway/)).

## Deployment areas

Each area owns its code under a dedicated package. One CDK app per entity:
shared fleet, control-plane lifecycle API, and public forwarder API.

| Entity | Path | Purpose |
| --- | --- | --- |
| **Control plane** | [`opensre-infra/lambdas/api_control_plane/`](opensre-infra/lambdas/api_control_plane/) | Lambda lifecycle provisioning (IAM-protected `/v1/organizations/.../gateway` routes), tenant lifecycle, AWS adapters. Symlinked at [`api_control_plane/`](api_control_plane/) for imports. |
| **Control-plane IaC** | [`opensre-infra/modules/api_control_plane`](opensre-infra/modules/api_control_plane/) | Terraform module: runtime Lambda, least-privilege roles, IAM HTTP API, and logs. |
| **Public API** | [`opensre-infra/lambdas/api_public_forwarder/`](opensre-infra/lambdas/api_public_forwarder/) | Bearer-authorizer-backed `/v1/runs` routes and Lambda composition root. Symlinked at [`api_public_forwarder/`](api_public_forwarder/) for imports. |
| **Public API IaC** | [`opensre-infra/modules/api_public_forwarder`](opensre-infra/modules/api_public_forwarder/) | Terraform module: public-run Lambda, REQUEST authorizer, routes, and logs. |
| **Shared fleet (IaC)** | [`fargate_fleet_infrastructure/`](fargate_fleet_infrastructure/) | Python CDK stack for ECS cluster, gateway security group, log group, and execution role. |
| **opensre-infra** | [`opensre-infra/`](opensre-infra/) | Git submodule of [opensre-infra-aws](https://github.com/Tracer-Cloud/opensre-infra-aws/tree/main): Lambda sources, shared S3 Files / memories Terraform, plus `stacks/fargate` (see [DEPLOYMENT.md](../../DEPLOYMENT.md)). |
| **Deploy scripts** | [`scripts/`](scripts/) | Fleet deploy helpers that resolve Terraform `memories` into CDK parameters. |
| **Gateway runtime** | [`../../gateway/`](../../gateway/) | Tenant Gateway process (Fargate task or legacy EC2/systemd). |

Shared HTTP helpers for both Lambda handlers live in [`utils/http_lambda.py`](utils/http_lambda.py).

Entry points:

- Control plane: `platform.deployment_fargate.api_control_plane.runtime.lambda_handler`
- Public forwarder: `platform.deployment_fargate.api_public_forwarder.runtime.lambda_handler`

## Fargate fleet (CDK)

| Command | What it does |
| --- | --- |
| `make cdk-synth` | Synthesize fleet CDK template |
| `make cdk-deploy-fleet` | Deploy ECS cluster, gateway SG, log group, execution role |
| `make cdk-deploy-fleet-from-infra-aws` | Deploy fleet with S3 Files parameters from opensre-infra-aws Terraform |
| `make cdk-deploy` | Deploy fleet CDK stack |
| `make cdk-destroy` | Tear down fleet CDK stack |
| `make cdk-verify` | Run fleet CDK synth tests + lambda bundle path check (no AWS credentials) |

Control-plane and public-forwarder Lambdas deploy via Terraform in
`opensre-infra/stacks/fargate` (see [DEPLOYMENT.md](../../DEPLOYMENT.md)).

Shared S3 Files storage (backing bucket + filesystem) is provisioned in
[opensre-infra-aws](https://github.com/Tracer-Cloud/opensre-infra-aws/tree/main),
vendored here as the [`opensre-infra/`](opensre-infra/) git submodule. After
`git submodule update --init platform/deployment_fargate/opensre-infra`, deploy
with the script or Make target:

```bash
# one-time Terraform backend init in the submodule
cd platform/deployment_fargate/opensre-infra/stacks/shared && terraform init -input=false

./platform/deployment_fargate/scripts/cdk_deploy_fleet_from_infra_aws.sh \
  --environment dev \
  --parameters VpcId=vpc-... \
  --parameters PublicSubnetIds=subnet-a,subnet-b \
  --parameters GatewayImage=... \
  --parameters CredentialsApiUrl=...
```

Field mapping and notes live in
[fargate_fleet_infrastructure/README.md](fargate_fleet_infrastructure/README.md).

See [fargate_fleet_infrastructure/README.md](fargate_fleet_infrastructure/README.md),
[opensre-infra/lambdas/README.md](opensre-infra/lambdas/README.md),
and [`.env.fargate-fleet.example`](../../.env.fargate-fleet.example) for local
control-plane runs outside the deployed Lambda.

## Related: EC2 Telegram gateway

The AMI + systemd Telegram gateway deploy path lives in
[`../deployment_ec2/telegram_gateway/`](../deployment_ec2/telegram_gateway/).
Makefile targets: `make bake-gateway`, `make deploy-gateway`, `make destroy-gateway`.

Shared helpers still in this package:

| Path | Purpose |
| --- | --- |
| [`utils/`](utils/) | Shared helpers: EC2 deploy env validation (`prep_ec2_deployment`), health polling, existing-infrastructure validation. |

The Slack backend (web API + Slack gateway) is **not** in this repo — it is
deployed and operated separately.

For gateway env vars and deploy prerequisites, see
[telegram_gateway/README.md](../deployment_ec2/telegram_gateway/README.md).

### E2E test infrastructure (separate from gateway deploy)

These Makefile targets provision **test-case** AWS stacks for the e2e suite, not the OpenSRE runtime:

| Command | Stack |
| --- | --- |
| `make deploy-lambda` / `make destroy-lambda` | Lambda test fixture |
| `make deploy-prefect` / `make destroy-prefect` | Prefect ECS Fargate fixture |
| `make deploy-flink` / `make destroy-flink` | Flink ECS fixture |

## Cloud-OpsBench AWS infrastructure

The Terraform module for running Cloud-OpsBench on AWS Fargate lives with the
benchmark code at
[`tests/benchmarks/cloudopsbench/infra/`](../../tests/benchmarks/cloudopsbench/infra/).
The one-time Terraform state bootstrap script lives at
[`tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh`](../../tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh).
See that directory's [README](../../tests/benchmarks/cloudopsbench/infra/README.md)
and the benchmark runner guide at
[`tests/benchmarks/cloudopsbench/README.md`](../../tests/benchmarks/cloudopsbench/README.md).
