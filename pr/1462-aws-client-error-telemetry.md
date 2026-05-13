Fixes #1462

#### Describe the changes you've made

Adds AWS-specific service-client telemetry for swallowed errors in `aws_sdk_client.py` and `lambda_client.py`.

The new helper tags AWS failures consistently with service, operation, region, function name, and AWS error code. `ClientError` failures are reported at warning severity, while unexpected runtime failures are reported at error severity.

Lambda code download, zip extraction, CloudWatch metric parsing, and invoke-payload JSON parsing now report the previously silent failure path while preserving the existing user-facing result shape.

### Demo/Screenshot

N/A - telemetry and regression-test change.

---

## Code Understanding and AI Usage

**Did you use AI assistance?**  
Yes.

## Checklist before requesting a review

- [x] I have performed a self-review of my code
- [x] I have added tests that prove my fix is effective or that my feature works
- [ ] `make test-cov` passes locally
- [ ] `make lint` passes locally
- [ ] `make typecheck` passes locally

Local verification:

- `C:\Users\11301\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m compileall app\services\aws app\services\aws_sdk_client.py app\services\lambda_client.py tests\services\test_aws_sdk_client.py tests\services\test_lambda_client.py tests\services\test_aws_telemetry.py`
- Pytest could not be run because neither the system Python nor bundled Python has the repo test dependencies installed, and `uv` is unavailable.
