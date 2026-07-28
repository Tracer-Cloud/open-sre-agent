# Shared Fargate fleet (Python CDK)

This CDK app owns the **stable shared foundation** for the multi-tenant Gateway
control plane. The Terraform modules
[`opensre-infra/modules/api_control_plane`](../opensre-infra/modules/api_control_plane/)
and
[`opensre-infra/modules/api_public_forwarder`](../opensre-infra/modules/api_public_forwarder/)
consume its outputs. Per-organization ECS services, task definitions, IAM task
roles, Secrets Manager records, and S3 Files access points remain in the Python
tenant lifecycle under
[`../opensre-infra/lambdas/api_control_plane/`](../opensre-infra/lambdas/api_control_plane/).

## What this stack creates

| Resource | Purpose |
| --- | --- |
| ECS Fargate cluster | Logical namespace for all tenant Gateway services |
| Gateway task security group | Outbound-only; no inbound rules |
| S3 Files mount-target security group | Accepts NFS only from Gateway tasks |
| CloudWatch log group | Shared `awslogs` destination for Gateway containers |
| ECS task execution role | Image pull + log write (managed execution policy) |

## What you must supply (parameters)

These resources are **not** created by this stack — pass explicit identifiers at deploy time:

| Parameter | Maps to env var |
| --- | --- |
| `VpcId` | (network placement only) |
| `PublicSubnetIds` | `OPENSRE_FARGATE_SUBNET_IDS` |
| `S3FileSystemId` | `OPENSRE_S3_FILESYSTEM_ID` |
| `S3FileSystemArn` | `OPENSRE_S3_FILESYSTEM_ARN` |
| `S3FilesClientSecurityGroupId` | included in `OPENSRE_FARGATE_SECURITY_GROUP_IDS` |
| `GatewayImage` | `OPENSRE_GATEWAY_IMAGE` |
| `CredentialsApiUrl` | `OPENSRE_CREDENTIALS_API_URL` |
| `ResourcePrefix` | `OPENSRE_FARGATE_RESOURCE_PREFIX` (optional, default `opensre`) |

Stack outputs echo the values needed by
[`TenantFleetConfig`](../opensre-infra/lambdas/api_control_plane/utils/get_fleet_config.py).
`OPENSRE_FARGATE_SECURITY_GROUP_IDS` joins the stack-created Gateway task security
group with the external S3 Files client security group so tasks can mount the
filesystem provisioned by
[opensre-infra-aws](https://github.com/Tracer-Cloud/opensre-infra-aws/tree/main).
The control-plane lifecycle ensures the existing filesystem has one mount target
in every configured public subnet using the mount-target security group.

## Bridging opensre-infra-aws Terraform outputs

Shared memories storage (backing bucket + S3 Files filesystem) is owned by
[opensre-infra-aws](https://github.com/Tracer-Cloud/opensre-infra-aws/tree/main)
(`stacks/shared` → `memories` output). This package references that repository as
the git submodule [`../opensre-infra/`](../opensre-infra/) (also usable in Cursor
as `@opensre-infra`).

| Terraform `memories[env]` field | Fleet CDK parameter |
| --- | --- |
| `file_system_id` | `S3FileSystemId` |
| `file_system_arn` | `S3FileSystemArn` |
| `client_security_group_id` | `S3FilesClientSecurityGroupId` |

Keep these explicit (not taken from Terraform):

- `VpcId`
- `PublicSubnetIds` (must be public subnets with an IGW route; prefer a subset of
  the infra `subnet_ids` so mount targets exist)
- `GatewayImage`
- `CredentialsApiUrl`

The infra backing bucket currently uses SSE-S3 (`AES256`) with public access
blocked. OpenSRE's optional `ExistingInfrastructureValidator` still expects
SSE-KMS when you run that helper — the Terraform bridge does not claim KMS
compliance and does not inject the raw bucket name into the fleet stack (runtime
consumes the S3 Files filesystem, not direct S3 object APIs).

Prerequisites:

```bash
git submodule update --init platform/deployment_fargate/opensre-infra
cd platform/deployment_fargate/opensre-infra/stacks/shared
terraform init -input=false
```

Deploy via the package script (preferred) or Make:

```bash
./platform/deployment_fargate/scripts/cdk_deploy_fleet_from_infra_aws.sh \
  --environment dev \
  --parameters VpcId=vpc-abc123 \
  --parameters PublicSubnetIds=subnet-a,subnet-b \
  --parameters GatewayImage=123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --parameters CredentialsApiUrl=https://credentials.example.com

# equivalent Make entrypoint (defaults OPENSRE_INFRA_AWS_DIR to the submodule)
make cdk-deploy-fleet-from-infra-aws \
  OPENSRE_INFRA_AWS_ENVIRONMENT=dev \
  FLEET_CDK_ARGS='--parameters VpcId=vpc-abc123 \
    --parameters PublicSubnetIds=subnet-a,subnet-b \
    --parameters GatewayImage=123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    --parameters CredentialsApiUrl=https://credentials.example.com'
```

Inspect resolved values without deploying:

```bash
./platform/deployment_fargate/scripts/cdk_deploy_fleet_from_infra_aws.sh \
  --environment dev \
  --print-json
```

## Prerequisites

1. **Install project deps with CDK extra** (from repo root):

   ```bash
   make install
   uv sync --extra cdk
   ```

2. **Install AWS CDK CLI** (once per machine, **v2.1133.0 or newer** to match `aws-cdk-lib`):

   ```bash
   npm install -g aws-cdk@2
   cdk --version
   ```

3. **Bootstrap the target account/region** (once per account/region):

   ```bash
   cd platform/deployment_fargate/fargate_fleet_infrastructure
   cdk bootstrap aws://ACCOUNT_ID/REGION
   ```

4. **AWS credentials** with permissions for ECS, EC2 (security groups), IAM, and CloudWatch Logs.

## Commands

From repo root:

```bash
make cdk-synth       # synthesize fleet and control-plane templates
make cdk-deploy-fleet FLEET_CDK_ARGS='--parameters ...'
make cdk-deploy-fleet-from-infra-aws OPENSRE_INFRA_AWS_DIR=... OPENSRE_INFRA_AWS_ENVIRONMENT=dev FLEET_CDK_ARGS='--parameters ...'
make cdk-deploy      # deploy fleet, then control plane
make cdk-destroy     # destroy control plane, then fleet
make cdk-verify      # run synth-level tests (no AWS credentials required)
```

Example deploy with parameters (manual; use `cdk-deploy-fleet-from-infra-aws` to
fill the S3 Files values from Terraform):

```bash
cd platform/deployment_fargate/fargate_fleet_infrastructure
uv run --extra cdk cdk deploy OpensreFargateFleet \
  --parameters VpcId=vpc-abc123 \
  --parameters PublicSubnetIds=subnet-a,subnet-b \
  --parameters S3FileSystemId=fs-123 \
  --parameters S3FileSystemArn=arn:aws:s3files:us-east-1:123456789012:file-system/fs-123 \
  --parameters S3FilesClientSecurityGroupId=sg-client \
  --parameters GatewayImage=123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --parameters CredentialsApiUrl=https://credentials.example.com
```

The Terraform public-forwarder and control-plane modules in
[`opensre-infra/stacks/fargate`](../opensre-infra/stacks/fargate/) take the
equivalent values as input variables (or read fleet foundation from the same
stack).
