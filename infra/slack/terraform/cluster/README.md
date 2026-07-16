# OpenSRE shared ECS cluster (Terraform)

Creates the single ECS cluster that all OpenSRE teams share. Apply this once
per environment; each team then applies the per-team stack in [`../`](../)
against this cluster.

One cluster, one silo ECS service per team. ECS clusters are free — the
isolation boundary is the per-team task role + security group in the per-team
stack, not the cluster.

## Usage

```bash
cd infra/slack/terraform/cluster
terraform init
terraform apply -var cluster_name=opensre-shared
```

Then, from the per-team stack, pass the same name:

```bash
cd ../
terraform apply -var cluster_name=opensre-shared -var team=dogfood -var env=dev ...
```

## Variables

- `cluster_name` — cluster name the per-team stack looks up (default
  `opensre-shared`).
- `container_insights` — `disabled` (default, no cost), `enabled`, or
  `enhanced`.
- `region` — AWS region (default `us-east-1`).
