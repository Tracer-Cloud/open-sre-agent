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

- Valid AWS credentials available to the environment (any standard boto3 chain):
  - AWS_ACCESS_KEY_ID
  - AWS_SECRET_ACCESS_KEY
  - AWS_SESSION_TOKEN (if required)
  - AWS_DEFAULT_REGION or AWS_REGION
- boto3 and requests installed in the running environment (the repo dev env
  normally provides these).
- A real Lambda function name to test (example: my-lambda-function). Use a
  non-production function or redact sensitive output before posting evidence.

Verification steps (single-file Python invocations)

Notes:
- These commands call the same Python functions used by the tool wrappers.
- Run them from the repo root so the package imports resolve.
- Redact any customer or host-identifying details before adding evidence to the
  issue.

1) Verify AWS verifier (optional sanity check):

   uv run opensre integrations verify aws

   Expectation: verifier passes and reports account access. If it fails, stop
   and fix credentials first — do not proceed to claim the tools.

2) get_lambda_configuration (lightweight configuration)

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_config_tool import get_lambda_configuration; print(json.dumps(get_lambda_configuration('my-lambda-function'), indent=2))"

   Expected: JSON with found=true and fields like runtime, handler, timeout.

3) inspect_lambda_function (configuration + optional code)

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_inspect_tool import inspect_lambda_function; print(json.dumps(inspect_lambda_function('my-lambda-function', include_code=False), indent=2))"

   Expected: JSON similar to configuration; when include_code=True the tool may
   return extracted files (if small) — redact file contents or only show file
   names and counts.

4) get_lambda_invocation_logs (invocation logs, recent invocations)

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_invocation_logs_tool import get_lambda_invocation_logs; print(json.dumps(get_lambda_invocation_logs('my-lambda-function', limit=20), indent=2))"

   Expected: JSON with invocation_count, invocations (summaries), or recent_logs.

5) get_lambda_errors (filtered errors)

   uv run python -c "import json; from integrations.aws_lambda.tools.lambda_errors_tool import get_lambda_errors; print(json.dumps(get_lambda_errors('my-lambda-function', limit=50), indent=2))"

   Expected: JSON with error-focused logs (same shape as invocation logs but
   filtered). An empty result is a valid outcome but must be reported as such.

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
