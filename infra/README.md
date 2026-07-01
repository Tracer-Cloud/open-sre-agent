# `infra/`

Infrastructure code for opensre local development and deployment.

## What's here

| Path | Purpose |
| --- | --- |
| [`aws/`](aws/) | Shared AWS SDK primitives (`client`, `config`, VPC/SG, EC2/IAM, ECR, SSM). |
| [`deploy/`](deploy/) | EC2 provisioning for web or gateway mode (`make deploy MODE=web|gateway`). |
| `docker-compose.*.yml` | Local development environments (database, RabbitMQ, testing). |
| `install-proxy/` | Install proxy utility. |

## Cloud-OpsBench AWS infrastructure

The Terraform module for running Cloud-OpsBench on AWS Fargate lives with the
benchmark code at
[`tests/benchmarks/cloudopsbench/infra/`](../tests/benchmarks/cloudopsbench/infra/).
The one-time Terraform state bootstrap script lives at
[`tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh`](../tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh).
See that directory's [README](../tests/benchmarks/cloudopsbench/infra/README.md)
and the benchmark runner guide at
[`tests/benchmarks/cloudopsbench/README.md`](../tests/benchmarks/cloudopsbench/README.md).
