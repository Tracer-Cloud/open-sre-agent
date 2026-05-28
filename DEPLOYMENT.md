## Deployment

OpenSRE deploys as a standard Python/FastAPI runtime. Use the repo `Dockerfile`,
Railway, EC2, ECS, Vercel, or another ASGI-capable host.

## Runtime Environment

1. Deploy this repository using your hosting provider's normal app workflow.
2. Configure your model provider in deployment environment variables:
    - `LLM_PROVIDER` (for example `anthropic`, `openai`, `openrouter`, `gemini`)
3. Add the matching provider API key:
    - `ANTHROPIC_API_KEY` when `LLM_PROVIDER=anthropic`
    - `OPENAI_API_KEY` when `LLM_PROVIDER=openai`
    - `OPENROUTER_API_KEY` when `LLM_PROVIDER=openrouter`
    - `GEMINI_API_KEY` when `LLM_PROVIDER=gemini`
4. Add `DATABASE_URI` and `REDIS_URI` for hosted layouts that need persistence.
5. Add any additional environment variables required by your integrations.
6. Deploy and verify service health.

Example minimum environment:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

The full set of supported provider keys and optional model overrides is documented in
`.env.example`.

---

## Railway Deployment

Railway remains available as a hosted runtime option.

Before deploying on Railway, make sure the target project has both Postgres and
Redis services, and that your OpenSRE service has `DATABASE_URI` and `REDIS_URI`
set to those connection strings.

If the deploy starts but the service never becomes healthy, verify that
`DATABASE_URI` and `REDIS_URI` are present on the Railway service and point to the
project Postgres and Redis instances.

## Remote Hosted Ops (Railway)

After deploying a hosted Railway service, you can run post-deploy operations from
the CLI:

```bash
# inspect service status, URL, deployment metadata
opensre remote ops --provider railway --project <project> --service <service> status

# tail recent logs
opensre remote ops --provider railway --project <project> --service <service> logs --lines 200

# stream logs live
opensre remote ops --provider railway --project <project> --service <service> logs --follow

# trigger restart/redeploy
opensre remote ops --provider railway --project <project> --service <service> restart --yes
```

OpenSRE saves your last used `provider`/`project`/`service`, so you can run:

```bash
opensre remote ops status
opensre remote ops logs --follow
```

---
