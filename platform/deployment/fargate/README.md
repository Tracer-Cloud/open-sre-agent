# `platform/deployment/fargate/`

ECS Fargate + RDS layout for OpenSRE web API and Slack Socket Mode gateway.

## Status

**Plan / dry-run only.** Live AWS provision is not implemented yet and is
gated behind `OPENSRE_FARGATE_CONFIRM=1`.

## Commands

```bash
make deploy-fargate ENV=staging          # plan (no AWS calls)
make deploy-fargate-apply ENV=staging    # refuses unless OPENSRE_FARGATE_CONFIRM=1
```

Equivalent:

```bash
uv run python -m platform.deployment.fargate.lifecycle plan --env staging
```

## Intended resources

See overview doc §4. Secrets under `/opensre/{env}/…`.
