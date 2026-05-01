## Official Deployment: LangGraph Platform

OpenSRE's official deployment method is LangGraph Platform.

## Deploy OpenSRE on LangGraph

1. Create a new deployment in LangGraph Platform.
2. Connect this repository to that deployment.
3. Ensure `langgraph.json` is present at the repository root (it defines the graph and
   HTTP app entrypoints).
4. Configure your model provider in deployment environment variables:
    - `LLM_PROVIDER` (for example `anthropic`, `openai`, `openrouter`, `gemini`)
5. Add the matching provider API key:
    - `ANTHROPIC_API_KEY` when `LLM_PROVIDER=anthropic`
    - `OPENAI_API_KEY` when `LLM_PROVIDER=openai`
    - `OPENROUTER_API_KEY` when `LLM_PROVIDER=openrouter`
    - `GEMINI_API_KEY` when `LLM_PROVIDER=gemini`
6. Add any additional environment variables required by your integrations.
7. Deploy and verify the service health in LangGraph Platform.

Example minimum environment:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

The full set of supported provider keys and optional model overrides is documented in
`.env.example`.

---

## Railway Deployment (Self-Hosted Alternative)

Railway remains available if you prefer a self-hosted deployment path.

Before running `opensre deploy railway`, make sure the target Railway project has
both Postgres and Redis services, and that your OpenSRE service has `DATABASE_URI`
and `REDIS_URI` set to those connection strings.

```bash
opensre deploy railway --project <project> --service <service> --yes
```

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
