"""AWS environment variable names."""

from __future__ import annotations

AWS_ROLE_ARN_ENV = "AWS_ROLE_ARN"
AWS_EXTERNAL_ID_ENV = "AWS_EXTERNAL_ID"
AWS_REGION_ENV = "AWS_REGION"
AWS_ACCESS_KEY_ID_ENV = "AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY_ENV = "AWS_SECRET_ACCESS_KEY"
AWS_SESSION_TOKEN_ENV = "AWS_SESSION_TOKEN"

#: boto3 reads these two as one unit: an access key id in the environment
#: without its secret does not fall through to the next credential source, it
#: raises ``PartialCredentialsError``. The secret is keyring-only, so a plain
#: ``.env`` can legitimately hold the id alone — it must then stay out of the
#: process environment.
AWS_STATIC_KEY_PAIR_ENV: tuple[str, str] = (AWS_ACCESS_KEY_ID_ENV, AWS_SECRET_ACCESS_KEY_ENV)

# Bedrock-only identity override, isolated from the vars above. Investigation
# tools (EC2, CloudWatch, ...) already read the ambient AWS_PROFILE directly,
# so a laptop configured for one AWS account already investigates that
# account correctly. Bedrock had no equivalent override, so a cross-account
# setup (Bedrock reachable only via an AssumeRole profile in a different
# account than the infra being investigated) could not give Bedrock a
# different identity than whatever profile was active for everything else.
# See #4482.
BEDROCK_AWS_PROFILE_ENV = "BEDROCK_AWS_PROFILE"
BEDROCK_AWS_ROLE_ARN_ENV = "BEDROCK_AWS_ROLE_ARN"
BEDROCK_AWS_EXTERNAL_ID_ENV = "BEDROCK_AWS_EXTERNAL_ID"
BEDROCK_AWS_REGION_ENV = "BEDROCK_AWS_REGION"

__all__ = [
    "AWS_ACCESS_KEY_ID_ENV",
    "AWS_EXTERNAL_ID_ENV",
    "AWS_REGION_ENV",
    "AWS_ROLE_ARN_ENV",
    "AWS_SECRET_ACCESS_KEY_ENV",
    "AWS_SESSION_TOKEN_ENV",
    "AWS_STATIC_KEY_PAIR_ENV",
    "BEDROCK_AWS_EXTERNAL_ID_ENV",
    "BEDROCK_AWS_PROFILE_ENV",
    "BEDROCK_AWS_REGION_ENV",
    "BEDROCK_AWS_ROLE_ARN_ENV",
]
