# `@opensre-infra`

Folder reference for the external AWS infrastructure repository:

- **GitHub:** https://github.com/Tracer-Cloud/opensre-infra-aws/tree/main
- **Submodule checkout:** [`opensre-infra/`](opensre-infra/)
- **Clone URL:** `https://github.com/Tracer-Cloud/opensre-infra-aws.git`
- **Lambda runtimes:** [`opensre-infra/lambdas/`](opensre-infra/lambdas/)

Initialize:

```bash
git submodule update --init platform/deployment_fargate/opensre-infra
```

Deploy the shared Fargate fleet using Terraform `memories` outputs from this
submodule:

```bash
./platform/deployment_fargate/scripts/cdk_deploy_fleet_from_infra_aws.sh --help
```
