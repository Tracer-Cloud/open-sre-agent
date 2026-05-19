#!/usr/bin/env python3
"""Seed test credentials for a tenant in local/staging AWS Secrets Manager.

Usage (LocalStack):
    AWS_DEFAULT_REGION=us-east-1 \
    AWS_ACCESS_KEY_ID=test \
    AWS_SECRET_ACCESS_KEY=test \
    AWS_ENDPOINT_URL=http://localhost:4566 \
    python scripts/bootstrap_tenant_secrets.py --tenant acme --key DD_API_KEY --value secret123

Usage (real AWS):
    python scripts/bootstrap_tenant_secrets.py --tenant acme --key DD_API_KEY --value secret123

Options:
    --tenant    Tenant ID                          (required)
    --key       Credential key name                (required)
    --value     Secret value                       (required)
    --region    AWS region       [default: us-east-1]
    --prefix    Secret prefix    [default: healops]
    --endpoint  Custom endpoint URL (e.g. http://localhost:4566 for LocalStack)
    --force     Overwrite an existing secret without prompting
"""

from __future__ import annotations

import argparse
import os


def _build_client(region: str, endpoint_url: str | None):  # type: ignore[return]
    import boto3

    kwargs: dict = {"region_name": region}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("secretsmanager", **kwargs)


def _put_secret(
    client,
    secret_name: str,
    value: str,
    *,
    force: bool,
) -> None:
    from botocore.exceptions import ClientError

    try:
        client.create_secret(Name=secret_name, SecretString=value)
        print(f"Created: {secret_name}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ResourceExistsException", "InvalidRequestException"):
            if not force:
                answer = input(f"Secret '{secret_name}' already exists. Overwrite? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Skipped.")
                    return
            client.put_secret_value(SecretId=secret_name, SecretString=value)
            print(f"Updated: {secret_name}")
        else:
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap tenant secrets in Secrets Manager.")
    parser.add_argument("--tenant", required=True, help="Tenant ID")
    parser.add_argument("--key", required=True, help="Credential key name")
    parser.add_argument("--value", required=True, help="Secret value")
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--prefix", default=os.environ.get("VAULT_PREFIX", "healops"))
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("AWS_ENDPOINT_URL"),
        help="Custom endpoint URL (e.g. http://localhost:4566 for LocalStack)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite without prompting")
    args = parser.parse_args()

    secret_name = f"{args.prefix}/{args.tenant}/{args.key}"
    print(f"Region  : {args.region}")
    print(f"Endpoint: {args.endpoint or '(AWS default)'}")
    print(f"Secret  : {secret_name}")

    client = _build_client(args.region, args.endpoint)
    _put_secret(client, secret_name, args.value, force=args.force)


if __name__ == "__main__":
    main()
