"""Live end-to-end verification of a deployed Fargate control-plane stack.

Drives the whole tenant lifecycle through the public HTTP APIs only:

1. Assume the lifecycle admin role and SigV4-sign a PUT that provisions a
   tenant gateway (spinning up a real ECS Fargate task), capturing the
   one-time public API bearer credential.
2. Poll GET until the gateway reports RUNNING.
3. POST bearer-authenticated prompts to /v1/runs on the public forwarder
   ("What organization are you?" and a weather report) and poll each run to
   completion.
4. Stop and delete the gateway.

Usage:
    uv run python -m platform.deployment_fargate.scripts.e2e_fargate_verify \
        --control-plane-endpoint https://... \
        --public-forwarder-endpoint https://... \
        --organization-id org_tf_e2e \
        --lifecycle-role-arn arn:aws:iam::...:role/opensre-lifecycle-admin

Requires: the tenant bootstrap secret
``<prefix>/tenants/<organization_id>/credentials-api-bootstrap`` must already
exist (tenant onboarding is out of band by design), and the caller must be
able to assume the lifecycle role.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

_GATEWAY_RUNNING_TIMEOUT_SECONDS = 600
_RUN_COMPLETION_TIMEOUT_SECONDS = 600
_POLL_INTERVAL_SECONDS = 10

_ORGANIZATION_PROMPT = "What organization are you? Reply with your organization id."
_WEATHER_PROMPT = "Give me a short weather report for London today."


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            payload = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        payload = error.read()
        status = error.code
    try:
        decoded = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        decoded = {"raw": payload.decode(errors="replace")}
    return status, decoded


class ControlPlaneClient:
    """SigV4-signed lifecycle calls as the assumed lifecycle role."""

    def __init__(self, *, endpoint: str, role_arn: str, region: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._region = region
        assumed = boto3.client("sts").assume_role(
            RoleArn=role_arn,
            RoleSessionName="e2e-fargate-verify",
        )["Credentials"]
        self._credentials = Credentials(
            assumed["AccessKeyId"],
            assumed["SecretAccessKey"],
            assumed["SessionToken"],
        )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._endpoint}{path}"
        body = json.dumps(payload).encode() if payload is not None else b""
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        aws_request = AWSRequest(method=method, url=url, data=body, headers=headers)
        SigV4Auth(self._credentials, "execute-api", self._region).add_auth(aws_request)
        return _http(method, url, headers=dict(aws_request.headers), body=body or None)


class PublicForwarderClient:
    """Bearer-authenticated /v1/runs calls."""

    def __init__(self, *, endpoint: str, bearer_token: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

    def submit_run(self, prompt: str) -> dict[str, Any]:
        status, body = _http(
            "POST",
            f"{self._endpoint}/v1/runs",
            headers=self._headers,
            body=json.dumps({"prompt": prompt}).encode(),
        )
        if status != 202:
            raise RuntimeError(f"POST /v1/runs failed: {status} {body}")
        return dict(body["run"])

    def fetch_run(self, run_id: str) -> dict[str, Any]:
        status, body = _http(
            "GET",
            f"{self._endpoint}/v1/runs/{run_id}",
            headers=self._headers,
            body=None,
        )
        if status != 200:
            raise RuntimeError(f"GET /v1/runs/{run_id} failed: {status} {body}")
        return dict(body["run"])

    def run_to_completion(self, prompt: str) -> dict[str, Any]:
        run = self.submit_run(prompt)
        print(f"  queued run {run['id']}")
        deadline = time.monotonic() + _RUN_COMPLETION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            run = self.fetch_run(run["id"])
            if run["status"] in {"SUCCEEDED", "FAILED"}:
                return run
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"run {run['id']} did not complete: {run['status']}")


def _wait_for_running(client: ControlPlaneClient, organization_id: str) -> None:
    deadline = time.monotonic() + _GATEWAY_RUNNING_TIMEOUT_SECONDS
    path = f"/v1/organizations/{organization_id}/gateway"
    while time.monotonic() < deadline:
        status, body = client.request("GET", path)
        if status != 200:
            raise RuntimeError(f"GET gateway failed: {status} {body}")
        actual = body["deployment"]["actual_state"]
        print(f"  gateway actual_state={actual}")
        if actual == "RUNNING":
            return
        if actual == "FAILED":
            raise RuntimeError(f"gateway failed: {body['deployment']}")
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise TimeoutError("gateway did not reach RUNNING")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-plane-endpoint", required=True)
    parser.add_argument("--public-forwarder-endpoint", required=True)
    parser.add_argument("--organization-id", default="org_tf_e2e")
    parser.add_argument("--lifecycle-role-arn", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--keep-gateway",
        action="store_true",
        help="Skip the stop/delete teardown at the end",
    )
    args = parser.parse_args()

    control_plane = ControlPlaneClient(
        endpoint=args.control_plane_endpoint,
        role_arn=args.lifecycle_role_arn,
        region=args.region,
    )
    gateway_path = f"/v1/organizations/{args.organization_id}/gateway"
    failures: list[str] = []

    print(f"[1/5] PUT {gateway_path} (provision gateway)")
    status, body = control_plane.request("PUT", gateway_path, payload={})
    if status != 200:
        print(f"provision failed: {status} {body}")
        return 1
    print(f"  deployment: {body['deployment']['actual_state']}")
    bearer_token = body.get("api_credential")
    if not bearer_token:
        # Credential is one-shot on first provision; rotate to get a fresh one.
        print("  no credential in response; rotating api credential")
        status, rotated = control_plane.request(
            "POST",
            f"/v1/organizations/{args.organization_id}/api-credential/rotate",
        )
        if status != 200 or not rotated.get("api_credential"):
            print(f"credential rotate failed: {status} {rotated}")
            return 1
        bearer_token = rotated["api_credential"]
    print("  captured public API bearer credential")

    print("[2/5] waiting for gateway task to reach RUNNING")
    _wait_for_running(control_plane, args.organization_id)

    forwarder = PublicForwarderClient(
        endpoint=args.public_forwarder_endpoint,
        bearer_token=bearer_token,
    )

    print(f"[3/5] POST /v1/runs: {_ORGANIZATION_PROMPT!r}")
    organization_run = forwarder.run_to_completion(_ORGANIZATION_PROMPT)
    print(f"  status={organization_run['status']}")
    print(f"  result: {organization_run['result']}")
    if organization_run["status"] != "SUCCEEDED" or not organization_run["result"]:
        failures.append("organization-identity run did not succeed")
    elif args.organization_id.lower() not in str(organization_run["result"]).lower():
        print("  WARNING: result does not mention the organization id verbatim")

    print(f"[4/5] POST /v1/runs: {_WEATHER_PROMPT!r}")
    weather_run = forwarder.run_to_completion(_WEATHER_PROMPT)
    print(f"  status={weather_run['status']}")
    print(f"  result: {weather_run['result']}")
    if weather_run["status"] != "SUCCEEDED" or not weather_run["result"]:
        failures.append("weather-report run did not succeed")

    if args.keep_gateway:
        print("[5/5] teardown skipped (--keep-gateway)")
    else:
        print("[5/5] stopping and deleting gateway")
        status, body = control_plane.request("POST", f"{gateway_path}/stop")
        if status != 200:
            failures.append(f"stop failed: {status} {body}")
        status, body = control_plane.request("DELETE", gateway_path)
        if status != 200:
            failures.append(f"delete failed: {status} {body}")

    if failures:
        print("\nE2E FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nE2E PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
