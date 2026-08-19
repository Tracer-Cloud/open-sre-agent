Purpose

Provide an environment recipe and exact verification steps so issue #5105 can be
claimed. This file explains minimal prerequisites and runnable commands that
exercise the four registered aws_lambda tools without changing code.

Scope

- What to do: produce reproducible evidence that each tool returns real data
  from AWS in a developer environment.
- What not to do: do not add an integrations verifier, do not change tool code,
  do not add CI tests that require live AWS credentials.

Prerequisites

- Valid AWS credentials available to the environment (boto3 credential chain):
  - AWS_ACCESS_KEY_ID
  - AWS_SECRET_ACCESS_KEY
  - AWS_SESSION_TOKEN (if required)
- Region configuration: the tools use the environment variable AWS_REGION (falling
  back to "us-east-1") via the project's boto3 wrapper. Set AWS_REGION to the
  target region. Setting AWS_DEFAULT_REGION is harmless but not relied upon by
  the client helper.
- boto3 and requests installed in the running environment (the repo dev env
  normally provides these).
- A real Lambda function name to test (example: my-lambda-function). Use a
  non-production function or redact sensitive output before posting evidence.

Required IAM permissions (minimum)

The verification commands exercise Lambda and CloudWatch Logs APIs. The test
identity must have at least these actions on the target account / resources:

- lambda:GetFunctionConfiguration
- lambda:GetFunction
- lambda:ListFunctions (optional, used by list code path)
- logs:FilterLogEvents
- logs:GetLogEvents
- logs:DescribeLogGroups (optional, for discovery)

If you prefer a fine-grained role, scope these actions to the relevant function
ARN and the /aws/lambda/{function_name} log group.

Verification steps (single-file Python invocations)

Notes:
- These one-liners invoke the repository's internal tool wrappers. They are a
  developer-facing verification method; do not consider the Python symbol names
  to be public API. The tools are also callable from the interactive shell
  surfaces.
- Run from the repo root so package imports resolve.
- Redact any customer or host-identifying details before adding evidence to the
  issue.

1) Sanity check: AWS verifier (optional)

   uv run opensre integrations verify aws

   Expectation: verifier passes and reports account access. If it fails, stop
   and fix credentials first — do not proceed to claim the tools.

2) Lambda configuration (user-facing name: "Lambda configuration") — quick check

Developer command (internal):

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_config_tool import get_lambda_configuration as _cmd; print(json.dumps(_cmd('my-lambda-function'), indent=2))"

Sanitized example output:

   {
     "found": true,
     "function_name": "my-lambda-function",
     "runtime": "python3.12",
     "handler": "handler.main",
     "timeout": 300
   }

3) Lambda inspect (user-facing name: "Lambda config") — configuration + optional code

Developer command (internal):

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_inspect_tool import inspect_lambda_function as _cmd; print(json.dumps(_cmd('my-lambda-function', include_code=False), indent=2))"

Sanitized example output:

   {
     "found": true,
     "function_name": "my-lambda-function",
     "function_arn": "arn:aws:lambda:...:function:my-lambda-function",
     "runtime": "python3.12",
     "timeout": 300
   }

4) Invocation logs (user-facing name: "Lambda logs")

Developer command (internal):

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_invocation_logs_tool import get_lambda_invocation_logs as _cmd; print(json.dumps(_cmd('my-lambda-function', limit=20), indent=2))"

Sanitized example output:

   {
     "found": true,
     "function_name": "my-lambda-function",
     "invocation_count": 2,
     "invocations": [
       {"request_id": "r1", "duration_ms": 100, "memory_used_mb": 128}
     ]
   }

5) Errors (user-facing name: "Lambda errors")

Developer command (internal):

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_errors_tool import get_lambda_errors as _cmd; print(json.dumps(_cmd('my-lambda-function', limit=50), indent=2))"

Sanitized example output:

   {
     "found": true,
     "function_name": "my-lambda-function",
     "invocations": []
   }

If a tool returns only an error or an empty result, record the exact command and
output in the issue and file a follow-up bug if the output looks like a stub or
an unexpected failure.

Recording evidence

For each tool, paste a short evidence block in the issue comments consisting of:
- command used (exact copy/paste)
- truncated JSON output (redact keys, hostnames, tokens)
- whether it was run via the Python one-liner above or from an interactive shell

Out of scope (do not do here)

- Adding a verifier for `aws_lambda` (the issue explicitly forbids adding one).
- Rewriting client code or the tools themselves. If a tool fails and shows a
  design problem, open a separate bug referencing this issue.
- Adding CI tests that require live AWS credentials.
