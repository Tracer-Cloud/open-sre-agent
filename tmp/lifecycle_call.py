"""One-off SigV4 caller for the control-plane lifecycle API (local testing)."""

import json
import sys

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

ENDPOINT = "https://hq1od4t2vi.execute-api.us-east-1.amazonaws.com"
ROLE_ARN = "arn:aws:iam::395261708130:role/opensre-lifecycle-admin"


def main() -> None:
    method, path = sys.argv[1], sys.argv[2]
    body = sys.argv[3] if len(sys.argv) > 3 else ""

    sts = boto3.client("sts")
    creds = sts.assume_role(RoleArn=ROLE_ARN, RoleSessionName="lifecycle-cli")["Credentials"]
    session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    request = AWSRequest(
        method=method,
        url=f"{ENDPOINT}{path}",
        data=body.encode() if body else None,
        headers={"content-type": "application/json"} if body else {},
    )
    SigV4Auth(session.get_credentials(), "execute-api", "us-east-1").add_auth(request)
    response = requests.request(
        method,
        f"{ENDPOINT}{path}",
        headers=dict(request.headers),
        data=body.encode() if body else None,
        timeout=60,
    )
    print(response.status_code)
    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text)


if __name__ == "__main__":
    main()
