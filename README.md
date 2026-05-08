<div align="center">

<p align="center">
  <img src="docs/logo/opensre-logo-white.svg" alt="OpenSRE" width="360" />
</p>

<h1>OpenSRE: Build Your Own AI SRE Agents</h1>

<p>The open-source framework for AI SRE agents, and the training and evaluation environment they need to improve. Connect the 60+ tools you already run, define your own workflows, and investigate incidents on your own infrastructure.</p>

<p align="center">
  <a href="https://github.com/Tracer-Cloud/opensre/actions/workflows/ci.yml?branch=main"><img src="https://img.shields.io/github/actions/workflow/status/Tracer-Cloud/opensre/ci.yml?branch=main&style=for-the-badge" alt="CI status"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/releases"><img src="https://img.shields.io/github/v/release/Tracer-Cloud/opensre?include_prereleases&style=for-the-badge" alt="GitHub release"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=for-the-badge" alt="Apache 2.0 License"></a>
  <a href="https://discord.gg/7NTpevXf7w"><img src="https://img.shields.io/badge/Discord-Join%20Us-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://trendshift.io/repositories/25889" target="_blank">
    <img
      src="https://trendshift.io/api/badge/repositories/25889"
      alt="Tracer-Cloud%2Fopensre | Trendshift"
      style="height: 30px; width: auto;"
      height="30"
    />
  </a>
</p>

<p align="center">
  <strong>
    <a href="https://www.opensre.com/docs/quickstart">Quickstart</a> ·
    <a href="https://www.opensre.com/docs">Docs</a> ·
    <a href="https://opensre.com/docs/faq">FAQ</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

</div>

---

> 🚧 Public Alpha: Core workflows are usable for early exploration, though not yet fully stable. The project is in active development, and APIs and integrations may evolve

---

## Table of Contents

- [Why OpenSRE?](#why-opensre)
- [Install](#install)
- [Quick Start](#quick-start)
- [Official Deployment (LangGraph)](#official-deployment-langgraph-platform)
- [Development](#development)
- [How OpenSRE Works](#how-opensre-works)
- [Benchmark](#benchmark)
- [Capabilities](#capabilities)
- [Integrations](#integrations)
- [Contributing](#contributing)
- [Security](#security)
- [Telemetry](#telemetry)
- [License](#license)
- [Citations](#citations)

---

## Why OpenSRE?

When something breaks in production, the evidence is scattered across logs, metrics, traces, runbooks, and Slack threads. OpenSRE is an open-source framework for AI SRE agents that resolve production incidents, built to run on your own infrastructure.

We do that because SWE-bench<sup>1</sup> gave coding agents scalable training data and clear feedback. Production incident response still lacks an equivalent.

Distributed failures are slower, noisier, and harder to simulate and evaluate than local code tasks, which is why AI SRE, and AI for production debugging more broadly, remains unsolved.

OpenSRE is building _that_ missing layer:

> an open reinforcement learning environment for agentic infrastructure incident response, with end-to-end tests and synthetic incident simulations for realistic production failures

We do that by:

- building easy-to-deploy, customizable AI SRE agents for production incident investigation and response
- running scored synthetic RCA suites that check root-cause accuracy, required evidence, and adversarial red herrings [(tests/synthetic)](tests/synthetic/rds_postgres)
- running real-world end-to-end tests across cloud-backed scenarios including Kubernetes, EC2, CloudWatch, Lambda, ECS Fargate, and Flink [(tests/e2e)](tests/e2e)
- keeping semantic test-catalog naming so e2e vs synthetic and local vs cloud boundaries stay obvious [(tests/README.md)](tests/README.md)

Our mission is to build AI SRE agents on top of this, scale it to thousands of realistic infrastructure failure scenarios, and establish OpenSRE as the benchmark and training ground for AI SRE.

<sup>1</sup> https://arxiv.org/abs/2310.06770

---

## Install

The root installer URL auto-detects Unix shell vs PowerShell. Add `--main` when you want the latest rolling build from `main` instead of the latest stable release.

Latest stable release:

```bash
curl -fsSL https://install.opensre.com | bash
```

Latest build from `main`:

```bash
curl -fsSL https://install.opensre.com | bash -s -- --main
```

```bash
brew tap tracer-cloud/tap
brew install tracer-cloud/tap/opensre
```

```powershell
irm https://install.opensre.com | iex
```

<!--
```bash
pipx install opensre
``` -->

---

## Quick Start

Configure once, then pick how you want to run investigations:

```bash
opensre onboard
```

**Interactive prompt shell** — run `opensre` with no subcommand to enter the REPL (TTY required). Describe incidents in plain language, stream investigations, and use slash commands:

```bash
opensre
```

**Direct investigation** — run the agent once from your terminal against an alert file (no interactive shell):

```bash
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

Other useful commands:

```bash
opensre update
opensre uninstall   # remove opensre and all local data
```

### Interactive mode

With no subcommand, `opensre` starts a persistent REPL session — an incident response terminal in the style of Claude Code. Describe an alert in plain text, watch the investigation stream live, then ask follow-up questions that stay grounded in what just ran.

```bash
opensre
# › MongoDB orders cluster is dropping connections since 14:00 UTC
# ...live streaming investigation...
# › why was the connection pool exhausted?
# ...grounded follow-up answer...
# › /status
# › /exit
```

Slash commands: `/help`, `/status`, `/clear`, `/reset`, `/trust`, `/exit`. Ctrl+C cancels an in-flight investigation while keeping the session state intact.

---

## Official Deployment: LangGraph Platform

OpenSRE's official deployment path is LangGraph Platform.

1. Create a deployment on LangGraph Platform and connect this repository.
2. Keep `langgraph.json` at the repo root so LangGraph can load the graph entrypoint.
3. Add your model provider in environment variables (for example `LLM_PROVIDER=anthropic`).
4. Add the matching API key for your provider (for example `ANTHROPIC_API_KEY` or
   `OPENAI_API_KEY`).
5. Add any additional runtime env vars your deployment needs (for example integration
   credentials and optional storage settings).

Minimum LLM env setup:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

For other providers, set the same `LLM_PROVIDER` plus the matching key from
`.env.example` (for example `OPENAI_API_KEY`, `GEMINI_API_KEY`, or
`OPENROUTER_API_KEY`).

## Railway Deployment (Self-Hosted Alternative)

If you prefer a self-hosted path, you can still deploy to Railway.

Before running `opensre deploy railway`, make sure the target Railway project has
both Postgres and Redis services, and that your OpenSRE service has `DATABASE_URI`
and `REDIS_URI` set to those connection strings. The containerized LangGraph runtime
will not boot without those backing services wired in.

```bash
# create/link Railway Postgres and Redis first, then set DATABASE_URI and REDIS_URI
opensre deploy railway --project <project> --service <service> --yes
```

If the deploy starts but the service never becomes healthy, verify that
`DATABASE_URI` and `REDIS_URI` are present on the Railway service and point to the
project Postgres and Redis instances.

### Remote Hosted Ops

After deploying a hosted service, you can run post-deploy operations from the CLI:

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

OpenSRE saves your last used `provider`, so you can run:

```bash
opensre remote ops status
opensre remote ops logs --follow
```

---

## Development

> **New to OpenSRE?** See [SETUP.md](SETUP.md) for detailed platform-specific setup instructions, including Windows setup, environment configuration, and more.

Local development installs use [uv](https://docs.astral.sh/uv/getting-started/installation/) and a committed `uv.lock` (`make install` runs `uv sync --frozen --extra dev`). Install uv first, then:

```bash
git clone https://github.com/Tracer-Cloud/opensre
cd opensre
make install
# run opensre onboard to configure your local LLM provider
# and optionally validate/save Grafana, Datadog, Honeycomb, Coralogix, Slack, AWS, GitHub MCP, and Sentry integrations
opensre onboard
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

If you use VS Code, the repo now includes a ready-to-use devcontainer under [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json). Open the repo in VS Code and run `Dev Containers: Reopen in Container` to get the project on Python 3.13 with the contributor toolchain preinstalled. Keep Docker Desktop, OrbStack, Colima, or another Docker-compatible runtime running on the host, since VS Code devcontainers rely on your local Docker engine.

---

## How OpenSRE Works

<img 
  src="https://github.com/user-attachments/assets/936ab1f2-9bda-438d-9897-e8e9cd98e335" 
  width="1064" 
  height="568" 
  alt="opensre-how-it-works-github" 
/>

### Investigation Workflow

When an alert fires, OpenSRE automatically:

1. **Fetches** the alert context and correlated logs, metrics, and traces
2. **Reasons** across your connected systems to identify anomalies
3. **Generates** a structured investigation report with probable root cause
4. **Suggests** next steps and, optionally, executes remediation actions
5. **Posts** a summary directly to Slack or PagerDuty - no context switching needed

---

## Benchmark

Generate the benchmark report:

```shell
make benchmark
```

---

## Capabilities

|                                          |                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| 🔍 **Structured incident investigation** | Correlated root-cause analysis across all your signals                           |
| 📋 **Runbook-aware reasoning**           | OpenSRE reads your runbooks and applies them automatically                       |
| 🔮 **Predictive failure detection**      | Catch emerging issues before they page you                                       |
| 🔗 **Evidence-backed root cause**        | Every conclusion is linked to the data behind it                                 |
| 🤖 **Full LLM flexibility**              | Bring your own model — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, NVIDIA NIM |

---

## Integrations

OpenSRE connects to 60+ tools and services across the modern cloud stack, from LLM providers and observability platforms to infrastructure, databases, and incident management.

| Category                | Integrations                                                                                                                                                                                                                                                                                                                                           | Roadmap                                                                                                                                                                                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **AI / LLM Providers**  | Anthropic · OpenAI · Ollama · Google Gemini · OpenRouter · NVIDIA NIM · Bedrock                                                                                                                                                                                                                                                                        |                                                                                                                                                                                                                                                                    |
| **Observability**       | <img src="docs/assets/icons/grafana.webp" width="16"> Grafana (Loki · Mimir · Tempo) · <img src="docs/assets/icons/datadog.svg" width="16"> Datadog · Honeycomb · Coralogix · <img src="docs/assets/icons/cloudwatch.png" width="16"> CloudWatch · <img src="docs/assets/icons/sentry.png" width="16"> Sentry · Elasticsearch · Better Stack Telemetry | [Splunk](https://github.com/Tracer-Cloud/opensre/issues/319) · [New Relic](https://github.com/Tracer-Cloud/opensre/issues/139) · [Victoria Logs](https://github.com/Tracer-Cloud/opensre/issues/126)                                                               |
| **Infrastructure**      | <img src="docs/assets/icons/kubernetes.png" width="16"> Kubernetes · <img src="docs/assets/icons/aws.png" width="16"> AWS (S3 · Lambda · EKS · EC2 · Bedrock) · <img src="docs/assets/icons/gcp.jpg" width="16"> GCP · <img src="docs/assets/icons/azure.png" width="16"> Azure                                                                        | [Helm](https://github.com/Tracer-Cloud/opensre/issues/321) · [ArgoCD](https://github.com/Tracer-Cloud/opensre/issues/320)                                                                                                                                          |
| **Database**            | MongoDB · ClickHouse · PostgreSQL · MySQL · MariaDB · MongoDB Atlas · Azure SQL · Snowflake                                                                                                                                                                                                                                                            | [RDS](https://github.com/Tracer-Cloud/opensre/issues/125)                                                                                                                                                                                                          |
| **Data Platform**       | Apache Airflow · Apache Kafka · Apache Spark · Prefect · RabbitMQ                                                                                                                                                                                                                                                                                      |                                                                                                                                                                                                                                                                    |
| **Dev Tools**           | <img src="docs/assets/icons/github.webp" width="16"> GitHub · GitHub MCP · Bitbucket · GitLab                                                                                                                                                                                                                                                          |                                                                                                                                                                                                                                                                    |
| **Incident Management** | <img src="docs/assets/icons/pagerduty.png" width="16"> PagerDuty · Opsgenie · Jira · Alertmanager                                                                                                                                                                                                                                                      | [Trello](https://github.com/Tracer-Cloud/opensre/issues/361) · [ServiceNow](https://github.com/Tracer-Cloud/opensre/issues/314) · [incident.io](https://github.com/Tracer-Cloud/opensre/issues/317) · [Linear](https://github.com/Tracer-Cloud/opensre/issues/124) |
| **Communication**       | <img src="docs/assets/icons/slack.png" width="16"> Slack · Google Docs · Discord                                                                                                                                                                                                                                                                       | [Notion](https://github.com/Tracer-Cloud/opensre/issues/286) · [Teams](https://github.com/Tracer-Cloud/opensre/issues/138) · [WhatsApp](https://github.com/Tracer-Cloud/opensre/issues/360) · [Confluence](https://github.com/Tracer-Cloud/opensre/issues/313)     |
| **Agent Deployment**    | <img src="docs/assets/icons/vercel.png" width="16"> Vercel · <img src="docs/assets/icons/langsmith.png" width="16"> LangSmith · <img src="docs/assets/icons/aws.png" width="16"> EC2 · <img src="docs/assets/icons/aws.png" width="16"> ECS · Railway                                                                                                  |                                                                                                                                                                                                                                                                    |
| **Protocols**           | <img src="docs/assets/icons/mcp.svg" width="16"> MCP · <img src="docs/assets/icons/acp.png" width="16"> ACP · <img src="docs/assets/icons/openclaw.jpg" width="16"> OpenClaw                                                                                                                                                                           |                                                                                                                                                                                                                                                                    |

---

## Contributing

OpenSRE is community-built. Every integration, improvement, and bug fix makes it better for thousands of engineers. We actively review PRs and welcome contributors of all experience levels.

<p>
  <a href="https://discord.gg/7NTpevXf7w">
    <img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Join our Discord" />
  </a>
</p>

Good first issues are labeled [`good first issue`](https://github.com/Tracer-Cloud/opensre/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22). Ways to contribute:

- 🐛 Report bugs or missing edge cases
- 🔌 Add a new tool integration
- 📖 Improve documentation or runbook examples
- ⭐ Star the repo - it helps other engineers find OpenSRE

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

<p align="center">
  <a href="https://www.star-history.com/#Tracer-Cloud/opensre&Date">
    <img src="https://api.star-history.com/svg?repos=Tracer-Cloud/opensre&type=Date" alt="Star History Chart">
  </a>
</p>

Thanks goes to these amazing people:

<!-- readme: contributors -start -->
<p>
<a href="https://github.com/davincios"><img src="https://avatars.githubusercontent.com/u/33206282?v=4&s=48" width="48" height="48" alt="davincios" /></a> <a href="https://github.com/VaibhavUpreti"><img src="https://avatars.githubusercontent.com/u/85568177?v=4&s=48" width="48" height="48" alt="VaibhavUpreti" /></a> <a href="https://github.com/aliya-tracer"><img src="https://avatars.githubusercontent.com/u/233726347?v=4&s=48" width="48" height="48" alt="aliya-tracer" /></a> <a href="https://github.com/arnetracer"><img src="https://avatars.githubusercontent.com/u/203629234?v=4&s=48" width="48" height="48" alt="arnetracer" /></a> <a href="https://github.com/kylie-tracer"><img src="https://avatars.githubusercontent.com/u/256781109?v=4&s=48" width="48" height="48" alt="kylie-tracer" /></a> <a href="https://github.com/0xpaulx"><img src="https://avatars.githubusercontent.com/u/214484440?v=4&s=48" width="48" height="48" alt="0xpaulx" /></a> <a href="https://github.com/zeel2104"><img src="https://avatars.githubusercontent.com/u/72783325?v=4&s=48" width="48" height="48" alt="zeel2104" /></a> <a href="https://github.com/iamkalio"><img src="https://avatars.githubusercontent.com/u/89003403?v=4&s=48" width="48" height="48" alt="iamkalio" /></a> <a href="https://github.com/w3joe"><img src="https://avatars.githubusercontent.com/u/84664178?v=4&s=48" width="48" height="48" alt="w3joe" /></a> <a href="https://github.com/yeoreums"><img src="https://avatars.githubusercontent.com/u/62932875?v=4&s=48" width="48" height="48" alt="yeoreums" /></a>
<a href="https://github.com/anandgupta1202"><img src="https://avatars.githubusercontent.com/u/39819996?v=4&s=48" width="48" height="48" alt="anandgupta1202" /></a> <a href="https://github.com/rrajan94"><img src="https://avatars.githubusercontent.com/u/25589618?v=4&s=48" width="48" height="48" alt="rrajan94" /></a> <a href="https://github.com/vrk7"><img src="https://avatars.githubusercontent.com/u/108936058?v=4&s=48" width="48" height="48" alt="vrk7" /></a> <a href="https://github.com/cerencamkiran"><img src="https://avatars.githubusercontent.com/u/150190567?v=4&s=48" width="48" height="48" alt="cerencamkiran" /></a> <a href="https://github.com/edgarmb14"><img src="https://avatars.githubusercontent.com/u/268297669?v=4&s=48" width="48" height="48" alt="edgarmb14" /></a> <a href="https://github.com/lukegimza"><img src="https://avatars.githubusercontent.com/u/68860070?v=4&s=48" width="48" height="48" alt="lukegimza" /></a> <a href="https://github.com/ebrahim-sameh"><img src="https://avatars.githubusercontent.com/u/23136098?v=4&s=48" width="48" height="48" alt="ebrahim-sameh" /></a> <a href="https://github.com/shoaib050326"><img src="https://avatars.githubusercontent.com/u/266381026?v=4&s=48" width="48" height="48" alt="shoaib050326" /></a> <a href="https://github.com/venturevd"><img src="https://avatars.githubusercontent.com/u/269883753?v=4&s=48" width="48" height="48" alt="venturevd" /></a> <a href="https://github.com/shriyashsoni"><img src="https://avatars.githubusercontent.com/u/138931443?v=4&s=48" width="48" height="48" alt="shriyashsoni" /></a>
<a href="https://github.com/Devesh36"><img src="https://avatars.githubusercontent.com/u/142524747?v=4&s=48" width="48" height="48" alt="Devesh36" /></a> <a href="https://github.com/KindaJayant"><img src="https://avatars.githubusercontent.com/u/136953152?v=4&s=48" width="48" height="48" alt="KindaJayant" /></a> <a href="https://github.com/overcastbulb"><img src="https://avatars.githubusercontent.com/u/99129410?v=4&s=48" width="48" height="48" alt="overcastbulb" /></a> <a href="https://github.com/Yashkapure06"><img src="https://avatars.githubusercontent.com/u/61585443?v=4&s=48" width="48" height="48" alt="Yashkapure06" /></a> <a href="https://github.com/Davda-James"><img src="https://avatars.githubusercontent.com/u/151067328?v=4&s=48" width="48" height="48" alt="Davda-James" /></a> <a href="https://github.com/Abhinnavverma"><img src="https://avatars.githubusercontent.com/u/138097198?v=4&s=48" width="48" height="48" alt="Abhinnavverma" /></a> <a href="https://github.com/devankitjuneja"><img src="https://avatars.githubusercontent.com/u/55021449?v=4&s=48" width="48" height="48" alt="devankitjuneja" /></a> <a href="https://github.com/ramandagar"><img src="https://avatars.githubusercontent.com/u/89700171?v=4&s=48" width="48" height="48" alt="ramandagar" /></a> <a href="https://github.com/mvanhorn"><img src="https://avatars.githubusercontent.com/u/455140?v=4&s=48" width="48" height="48" alt="mvanhorn" /></a> <a href="https://github.com/abhishek-marathe04"><img src="https://avatars.githubusercontent.com/u/175933950?v=4&s=48" width="48" height="48" alt="abhishek-marathe04" /></a>
<a href="https://github.com/yashksaini-coder"><img src="https://avatars.githubusercontent.com/u/115717039?v=4&s=48" width="48" height="48" alt="yashksaini-coder" /></a> <a href="https://github.com/haliaeetusvocifer"><img src="https://avatars.githubusercontent.com/u/20953018?v=4&s=48" width="48" height="48" alt="haliaeetusvocifer" /></a> <a href="https://github.com/Bahtya"><img src="https://avatars.githubusercontent.com/u/34988899?v=4&s=48" width="48" height="48" alt="Bahtya" /></a> <a href="https://github.com/mayankbharati-ops"><img src="https://avatars.githubusercontent.com/u/245952278?v=4&s=48" width="48" height="48" alt="mayankbharati-ops" /></a> <a href="https://github.com/harshareddy832"><img src="https://avatars.githubusercontent.com/u/53609097?v=4&s=48" width="48" height="48" alt="harshareddy832" /></a> <a href="https://github.com/sundaram2021"><img src="https://avatars.githubusercontent.com/u/93595231?v=4&s=48" width="48" height="48" alt="sundaram2021" /></a> <a href="https://github.com/micheal000010000-hub"><img src="https://avatars.githubusercontent.com/u/249460313?v=4&s=48" width="48" height="48" alt="micheal000010000-hub" /></a> <a href="https://github.com/ljivesh"><img src="https://avatars.githubusercontent.com/u/96004270?v=4&s=48" width="48" height="48" alt="ljivesh" /></a> <a href="https://github.com/gautamjain1503"><img src="https://avatars.githubusercontent.com/u/97388837?v=4&s=48" width="48" height="48" alt="gautamjain1503" /></a> <a href="https://github.com/mudittt"><img src="https://avatars.githubusercontent.com/u/96051296?v=4&s=48" width="48" height="48" alt="mudittt" /></a>
<a href="https://github.com/hamzzaaamalik"><img src="https://avatars.githubusercontent.com/u/147706212?v=4&s=48" width="48" height="48" alt="hamzzaaamalik" /></a> <a href="https://github.com/octo-patch"><img src="https://avatars.githubusercontent.com/u/266937838?v=4&s=48" width="48" height="48" alt="octo-patch" /></a> <a href="https://github.com/fuleinist"><img src="https://avatars.githubusercontent.com/u/1163738?v=4&s=48" width="48" height="48" alt="fuleinist" /></a> <a href="https://github.com/yas789"><img src="https://avatars.githubusercontent.com/u/84193712?v=4&s=48" width="48" height="48" alt="yas789" /></a> <a href="https://github.com/sharkello"><img src="https://avatars.githubusercontent.com/u/159360024?v=4&s=48" width="48" height="48" alt="sharkello" /></a> <a href="https://github.com/kaushal-bakrania"><img src="https://avatars.githubusercontent.com/u/71706867?v=4&s=48" width="48" height="48" alt="kaushal-bakrania" /></a> <a href="https://github.com/darthwade"><img src="https://avatars.githubusercontent.com/u/2220776?v=4&s=48" width="48" height="48" alt="darthwade" /></a> <a href="https://github.com/aniruddhaadak80"><img src="https://avatars.githubusercontent.com/u/127435065?v=4&s=48" width="48" height="48" alt="aniruddhaadak80" /></a> <a href="https://github.com/chaosreload"><img src="https://avatars.githubusercontent.com/u/6723037?v=4&s=48" width="48" height="48" alt="chaosreload" /></a> <a href="https://github.com/paulovitorcl"><img src="https://avatars.githubusercontent.com/u/47778440?v=4&s=48" width="48" height="48" alt="paulovitorcl" /></a>
<a href="https://github.com/gbsierra"><img src="https://avatars.githubusercontent.com/u/182822327?v=4&s=48" width="48" height="48" alt="gbsierra" /></a> <a href="https://github.com/alexanderkreidich"><img src="https://avatars.githubusercontent.com/u/126781073?v=4&s=48" width="48" height="48" alt="alexanderkreidich" /></a> <a href="https://github.com/afif1400"><img src="https://avatars.githubusercontent.com/u/51887071?v=4&s=48" width="48" height="48" alt="afif1400" /></a> <a href="https://github.com/gauravch-code"><img src="https://avatars.githubusercontent.com/u/180489802?v=4&s=48" width="48" height="48" alt="gauravch-code" /></a> <a href="https://github.com/divijgera"><img src="https://avatars.githubusercontent.com/u/46404484?v=4&s=48" width="48" height="48" alt="divijgera" /></a> <a href="https://github.com/daxp472"><img src="https://avatars.githubusercontent.com/u/177292922?v=4&s=48" width="48" height="48" alt="daxp472" /></a> <a href="https://github.com/Som-0619"><img src="https://avatars.githubusercontent.com/u/143019791?v=4&s=48" width="48" height="48" alt="Som-0619" /></a> <a href="https://github.com/Gust-svg"><img src="https://avatars.githubusercontent.com/u/265007695?v=4&s=48" width="48" height="48" alt="Gust-svg" /></a> <a href="https://github.com/Sayeem3051"><img src="https://avatars.githubusercontent.com/u/169171880?v=4&s=48" width="48" height="48" alt="Sayeem3051" /></a> <a href="https://github.com/MachineLearning-Nerd"><img src="https://avatars.githubusercontent.com/u/37579156?v=4&s=48" width="48" height="48" alt="MachineLearning-Nerd" /></a>
<a href="https://github.com/F4tal1t"><img src="https://avatars.githubusercontent.com/u/109851148?v=4&s=48" width="48" height="48" alt="F4tal1t" /></a> <a href="https://github.com/MestreY0d4-Uninter"><img src="https://avatars.githubusercontent.com/u/241404605?v=4&s=48" width="48" height="48" alt="MestreY0d4-Uninter" /></a> <a href="https://github.com/qorexdevs"><img src="https://avatars.githubusercontent.com/u/277760369?v=4&s=48" width="48" height="48" alt="qorexdevs" /></a> <a href="https://github.com/Agnuxo1"><img src="https://avatars.githubusercontent.com/u/166046035?v=4&s=48" width="48" height="48" alt="Agnuxo1" /></a> <a href="https://github.com/Ryjen1"><img src="https://avatars.githubusercontent.com/u/114498519?v=4&s=48" width="48" height="48" alt="Ryjen1" /></a> <a href="https://github.com/nandanadileep"><img src="https://avatars.githubusercontent.com/u/110280757?v=4&s=48" width="48" height="48" alt="nandanadileep" /></a> <a href="https://github.com/Maharshi-Project"><img src="https://avatars.githubusercontent.com/u/156591746?v=4&s=48" width="48" height="48" alt="Maharshi-Project" /></a> <a href="https://github.com/udit-rawat"><img src="https://avatars.githubusercontent.com/u/84604012?v=4&s=48" width="48" height="48" alt="udit-rawat" /></a> <a href="https://github.com/muddlebee"><img src="https://avatars.githubusercontent.com/u/8139783?v=4&s=48" width="48" height="48" alt="muddlebee" /></a> <a href="https://github.com/Jah-yee"><img src="https://avatars.githubusercontent.com/u/166608075?v=4&s=48" width="48" height="48" alt="Jah-yee" /></a>
<a href="https://github.com/Sarah-Salah"><img src="https://avatars.githubusercontent.com/u/11881117?v=4&s=48" width="48" height="48" alt="Sarah-Salah" /></a> <a href="https://github.com/jerome-wilson"><img src="https://avatars.githubusercontent.com/u/116165488?v=4&s=48" width="48" height="48" alt="jerome-wilson" /></a> <a href="https://github.com/hcombalicer"><img src="https://avatars.githubusercontent.com/u/40112059?v=4&s=48" width="48" height="48" alt="hcombalicer" /></a> <a href="https://github.com/CuriousHet"><img src="https://avatars.githubusercontent.com/u/102606191?v=4&s=48" width="48" height="48" alt="CuriousHet" /></a> <a href="https://github.com/Dipxssi"><img src="https://avatars.githubusercontent.com/u/151428630?v=4&s=48" width="48" height="48" alt="Dipxssi" /></a> <a href="https://github.com/sirohikartik"><img src="https://avatars.githubusercontent.com/u/99896785?v=4&s=48" width="48" height="48" alt="sirohikartik" /></a> <a href="https://github.com/imjohnzakkam"><img src="https://avatars.githubusercontent.com/u/42964266?v=4&s=48" width="48" height="48" alt="imjohnzakkam" /></a> <a href="https://github.com/paarths-collab"><img src="https://avatars.githubusercontent.com/u/205314222?v=4&s=48" width="48" height="48" alt="paarths-collab" /></a> <a href="https://github.com/wahajahmed010"><img src="https://avatars.githubusercontent.com/u/57330918?v=4&s=48" width="48" height="48" alt="wahajahmed010" /></a> <a href="https://github.com/Ade20boss"><img src="https://avatars.githubusercontent.com/u/168012500?v=4&s=48" width="48" height="48" alt="Ade20boss" /></a>
<a href="https://github.com/MichaelGurevich"><img src="https://avatars.githubusercontent.com/u/105605801?v=4&s=48" width="48" height="48" alt="MichaelGurevich" /></a> <a href="https://github.com/SB2318"><img src="https://avatars.githubusercontent.com/u/87614560?v=4&s=48" width="48" height="48" alt="SB2318" /></a> <a href="https://github.com/Davidson3556"><img src="https://avatars.githubusercontent.com/u/99369614?v=4&s=48" width="48" height="48" alt="Davidson3556" /></a> <a href="https://github.com/gitsofaryan"><img src="https://avatars.githubusercontent.com/u/117700812?v=4&s=48" width="48" height="48" alt="gitsofaryan" /></a> <a href="https://github.com/GoDiao"><img src="https://avatars.githubusercontent.com/u/104132148?v=4&s=48" width="48" height="48" alt="GoDiao" /></a> <a href="https://github.com/7vignesh"><img src="https://avatars.githubusercontent.com/u/97684755?v=4&s=48" width="48" height="48" alt="7vignesh" /></a> <a href="https://github.com/turancannb02"><img src="https://avatars.githubusercontent.com/u/131914656?v=4&s=48" width="48" height="48" alt="turancannb02" /></a> <a href="https://github.com/ShivaniNR"><img src="https://avatars.githubusercontent.com/u/47320667?v=4&s=48" width="48" height="48" alt="ShivaniNR" /></a> <a href="https://github.com/0xDevNinja"><img src="https://avatars.githubusercontent.com/u/102245100?v=4&s=48" width="48" height="48" alt="0xDevNinja" /></a> <a href="https://github.com/blut-agent"><img src="https://avatars.githubusercontent.com/u/278569635?v=4&s=48" width="48" height="48" alt="blut-agent" /></a>
<a href="https://github.com/Ghraven"><img src="https://avatars.githubusercontent.com/u/115199279?v=4&s=48" width="48" height="48" alt="Ghraven" /></a> <a href="https://github.com/kespineira"><img src="https://avatars.githubusercontent.com/u/44882187?v=4&s=48" width="48" height="48" alt="kespineira" /></a> <a href="https://github.com/AarushSharmaa"><img src="https://avatars.githubusercontent.com/u/68619452?v=4&s=48" width="48" height="48" alt="AarushSharmaa" /></a> <a href="https://github.com/Lozsku"><img src="https://avatars.githubusercontent.com/u/98460727?v=4&s=48" width="48" height="48" alt="Lozsku" /></a> <a href="https://github.com/Piyushtiwari919"><img src="https://avatars.githubusercontent.com/u/184945555?v=4&s=48" width="48" height="48" alt="Piyushtiwari919" /></a> <a href="https://github.com/hruico"><img src="https://avatars.githubusercontent.com/u/218068869?v=4&s=48" width="48" height="48" alt="hruico" /></a> <a href="https://github.com/IBOCATA"><img src="https://avatars.githubusercontent.com/u/74919012?v=4&s=48" width="48" height="48" alt="IBOCATA" /></a> <a href="https://github.com/Jeel3011"><img src="https://avatars.githubusercontent.com/u/166152117?v=4&s=48" width="48" height="48" alt="Jeel3011" /></a> <a href="https://github.com/Gingiris"><img src="https://avatars.githubusercontent.com/u/260675847?v=4&s=48" width="48" height="48" alt="Gingiris" /></a> <a href="https://github.com/rameshkumarkoyya"><img src="https://avatars.githubusercontent.com/u/109403918?v=4&s=48" width="48" height="48" alt="rameshkumarkoyya" /></a>
<a href="https://github.com/JustInCache"><img src="https://avatars.githubusercontent.com/u/105823120?v=4&s=48" width="48" height="48" alt="JustInCache" /></a> <a href="https://github.com/Genmin"><img src="https://avatars.githubusercontent.com/u/90125084?v=4&s=48" width="48" height="48" alt="Genmin" /></a> <a href="https://github.com/WatchTree-19"><img src="https://avatars.githubusercontent.com/u/119982314?v=4&s=48" width="48" height="48" alt="WatchTree-19" /></a> <a href="https://github.com/cokerrd"><img src="https://avatars.githubusercontent.com/u/82083946?v=4&s=48" width="48" height="48" alt="cokerrd" /></a> <a href="https://github.com/jason8745"><img src="https://avatars.githubusercontent.com/u/41944427?v=4&s=48" width="48" height="48" alt="jason8745" /></a> <a href="https://github.com/Yajush-afk"><img src="https://avatars.githubusercontent.com/u/180868061?v=4&s=48" width="48" height="48" alt="Yajush-afk" /></a> <a href="https://github.com/Aaryan-549"><img src="https://avatars.githubusercontent.com/u/165829168?v=4&s=48" width="48" height="48" alt="Aaryan-549" /></a> <a href="https://github.com/CoderHariswar"><img src="https://avatars.githubusercontent.com/u/113418253?v=4&s=48" width="48" height="48" alt="CoderHariswar" /></a> <a href="https://github.com/zeesshhh0"><img src="https://avatars.githubusercontent.com/u/87911619?v=4&s=48" width="48" height="48" alt="zeesshhh0" /></a> <a href="https://github.com/PrakharJain345"><img src="https://avatars.githubusercontent.com/u/171273173?v=4&s=48" width="48" height="48" alt="PrakharJain345" /></a>
<a href="https://github.com/Bhavarth7"><img src="https://avatars.githubusercontent.com/u/76651028?v=4&s=48" width="48" height="48" alt="Bhavarth7" /></a> <a href="https://github.com/emefienem"><img src="https://avatars.githubusercontent.com/u/122095740?v=4&s=48" width="48" height="48" alt="emefienem" /></a> <a href="https://github.com/TejasS1233"><img src="https://avatars.githubusercontent.com/u/145673356?v=4&s=48" width="48" height="48" alt="TejasS1233" /></a> <a href="https://github.com/DsThakurRawat"><img src="https://avatars.githubusercontent.com/u/186957976?v=4&s=48" width="48" height="48" alt="DsThakurRawat" /></a> <a href="https://github.com/akshat1074"><img src="https://avatars.githubusercontent.com/u/138868940?v=4&s=48" width="48" height="48" alt="akshat1074" /></a> <a href="https://github.com/Diwansu-pilania"><img src="https://avatars.githubusercontent.com/u/192974860?v=4&s=48" width="48" height="48" alt="Diwansu-pilania" /></a> <a href="https://github.com/AniketR10"><img src="https://avatars.githubusercontent.com/u/169879837?v=4&s=48" width="48" height="48" alt="AniketR10" /></a> <a href="https://github.com/Jai0401"><img src="https://avatars.githubusercontent.com/u/112328542?v=4&s=48" width="48" height="48" alt="Jai0401" /></a> <a href="https://github.com/shivambehl"><img src="https://avatars.githubusercontent.com/u/41379568?v=4&s=48" width="48" height="48" alt="shivambehl" /></a> <a href="https://github.com/retr0-kernel"><img src="https://avatars.githubusercontent.com/u/82054542?v=4&s=48" width="48" height="48" alt="retr0-kernel" /></a>
<a href="https://github.com/IsaacOdeimor"><img src="https://avatars.githubusercontent.com/u/218982227?v=4&s=48" width="48" height="48" alt="IsaacOdeimor" /></a> <a href="https://github.com/RajGajjar-01"><img src="https://avatars.githubusercontent.com/u/153660066?v=4&s=48" width="48" height="48" alt="RajGajjar-01" /></a> <a href="https://github.com/4arjun"><img src="https://avatars.githubusercontent.com/u/144534911?v=4&s=48" width="48" height="48" alt="4arjun" /></a> <a href="https://github.com/cloudenochcsis"><img src="https://avatars.githubusercontent.com/u/155973884?v=4&s=48" width="48" height="48" alt="cloudenochcsis" /></a> <a href="https://github.com/Thibault00"><img src="https://avatars.githubusercontent.com/u/84420566?v=4&s=48" width="48" height="48" alt="Thibault00" /></a> <a href="https://github.com/umeraamir09"><img src="https://avatars.githubusercontent.com/u/130839691?v=4&s=48" width="48" height="48" alt="umeraamir09" /></a> <a href="https://github.com/aksKrIITK"><img src="https://avatars.githubusercontent.com/u/196282905?v=4&s=48" width="48" height="48" alt="aksKrIITK" /></a> <a href="https://github.com/zerone0x"><img src="https://avatars.githubusercontent.com/u/39543393?v=4&s=48" width="48" height="48" alt="zerone0x" /></a> <a href="https://github.com/Powlisher"><img src="https://avatars.githubusercontent.com/u/200061014?v=4&s=48" width="48" height="48" alt="Powlisher" /></a> <a href="https://github.com/vidhishah2209"><img src="https://avatars.githubusercontent.com/u/179381557?v=4&s=48" width="48" height="48" alt="vidhishah2209" /></a>
<a href="https://github.com/aayushprsingh"><img src="https://avatars.githubusercontent.com/u/172073271?v=4&s=48" width="48" height="48" alt="aayushprsingh" /></a> <a href="https://github.com/shubh586"><img src="https://avatars.githubusercontent.com/u/176175004?v=4&s=48" width="48" height="48" alt="shubh586" /></a> <a href="https://github.com/mazenessam77"><img src="https://avatars.githubusercontent.com/u/184118745?v=4&s=48" width="48" height="48" alt="mazenessam77" /></a> <a href="https://github.com/mstejas610"><img src="https://avatars.githubusercontent.com/u/116860222?v=4&s=48" width="48" height="48" alt="mstejas610" /></a> <a href="https://github.com/jeetjawale"><img src="https://avatars.githubusercontent.com/u/112877983?v=4&s=48" width="48" height="48" alt="jeetjawale" /></a> <a href="https://github.com/rudra496"><img src="https://avatars.githubusercontent.com/u/78224940?v=4&s=48" width="48" height="48" alt="rudra496" /></a> <a href="https://github.com/YauhenBichel"><img src="https://avatars.githubusercontent.com/u/5603242?v=4&s=48" width="48" height="48" alt="YauhenBichel" /></a> <a href="https://github.com/thisisharsh7"><img src="https://avatars.githubusercontent.com/u/95894045?v=4&s=48" width="48" height="48" alt="thisisharsh7" /></a> <a href="https://github.com/programmerloverun"><img src="https://avatars.githubusercontent.com/u/123237285?v=4&s=48" width="48" height="48" alt="programmerloverun" /></a> <a href="https://github.com/abhinavgautam01"><img src="https://avatars.githubusercontent.com/u/183635986?v=4&s=48" width="48" height="48" alt="abhinavgautam01" /></a>
<a href="https://github.com/arian24b"><img src="https://avatars.githubusercontent.com/u/45208666?v=4&s=48" width="48" height="48" alt="arian24b" /></a> <a href="https://github.com/Dhaxor"><img src="https://avatars.githubusercontent.com/u/46064597?v=4&s=48" width="48" height="48" alt="Dhaxor" /></a> <a href="https://github.com/vivek41-glitch"><img src="https://avatars.githubusercontent.com/u/222612958?v=4&s=48" width="48" height="48" alt="vivek41-glitch" /></a> <a href="https://github.com/nawneet77"><img src="https://avatars.githubusercontent.com/u/87024784?v=4&s=48" width="48" height="48" alt="nawneet77" /></a> <a href="https://github.com/Imsharad"><img src="https://avatars.githubusercontent.com/u/19369042?v=4&s=48" width="48" height="48" alt="Imsharad" /></a> <a href="https://github.com/Mohdtalibakhtar"><img src="https://avatars.githubusercontent.com/u/66231998?v=4&s=48" width="48" height="48" alt="Mohdtalibakhtar" /></a> <a href="https://github.com/Lum1104"><img src="https://avatars.githubusercontent.com/u/87774050?v=4&s=48" width="48" height="48" alt="Lum1104" /></a> <a href="https://github.com/VibhorGautam"><img src="https://avatars.githubusercontent.com/u/55019395?v=4&s=48" width="48" height="48" alt="VibhorGautam" /></a> <a href="https://github.com/samdiano"><img src="https://avatars.githubusercontent.com/u/27571135?v=4&s=48" width="48" height="48" alt="samdiano" /></a>
</p>
<!-- readme: contributors -end -->

---

## Security

OpenSRE is designed with production environments in mind:

- No storing of raw log data beyond the investigation session
- All LLM calls use structured, auditable prompts
- Log transcripts are kept locally - never sent externally by default

See [SECURITY.md](SECURITY.md) for responsible disclosure.

---

## Telemetry & privacy

`opensre` ships with two telemetry stacks, both opt-out:

- **PostHog** for anonymous product analytics (which commands are used, success/failure, rough runtime, CLI version, Python version, OS family, machine architecture, and a small amount of command-specific metadata such as which subcommand ran). For `opensre onboard` and `opensre investigate`, we may also collect the selected model/provider and whether the command used flags such as `--interactive` or `--input`.
- **Sentry** for crash and error reports (stack traces, environment, release tag).
  - Every event is tagged with `entrypoint` (`cli`, `webapp`, `remote`, `mcp`, `integrations`, `wizard`, `graph_pipeline`), `opensre.runtime` (`cli` for user-driven CLI/wizard surfaces, `hosted` for `webapp`/`remote`/`mcp`/`graph_pipeline` server surfaces — derived from the entrypoint, not the `ENV` var; the `opensre.` prefix avoids colliding with Sentry's built-in `runtime` Python-runtime context), and `deployment_method` (`railway`, `langsmith`, `local`). `in_app_include=["app"]` keeps agent frames marked in-app, and `LoggingIntegration`, `AsyncioIntegration` and `HttpxIntegration` are wired explicitly.
  - Scrubbing before transport: home-directory paths in stack traces; sensitive headers (`Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`); query strings on `http`/`httpx` breadcrumbs and the same headers on `http`/`httpx`/`aiohttp` breadcrumbs (defensive — the aiohttp filter only fires if a breadcrumb of that category is emitted); secret-looking keys, both by suffix (`*_token`, `*_key`, `*_secret`, `*_password`) and by substring (`prompt`, `messages`, `system_prompt`, `dsn`, `bearer`, `cookie`, `auth`, `credential`). The substring sweep is intentionally aggressive: keys like `auth_method` or `chat_messages` will be redacted. Request bodies (`request.data`/`request.body`) and `extra` payloads are walked recursively, so nested LLM payloads cannot leak through.

A randomly generated anonymous install ID is created on first run and stored in `~/.config/opensre/anonymous_id`. PostHog `distinct_id` values are scoped to that install ID, so unique-user counts represent unique CLI installs/devices rather than command invocations. One-time lifecycle events use deterministic event IDs to avoid duplicate rows if they are retried.

We never collect alert contents, file contents, hostnames, credentials, raw command arguments, or any other personally identifiable information. Telemetry is automatically disabled in GitHub Actions and pytest runs.

### Kill-switch matrix

| Env var | PostHog | Sentry |
| --- | --- | --- |
| `OPENSRE_NO_TELEMETRY=1` | disabled | disabled |
| `DO_NOT_TRACK=1` | disabled | disabled |
| `OPENSRE_ANALYTICS_DISABLED=1` | disabled | unaffected |
| `OPENSRE_SENTRY_DISABLED=1` | unaffected | disabled |

For full opt-out:

```bash
export OPENSRE_NO_TELEMETRY=1
```

### Overriding the Sentry DSN

Self-hosted users can route errors to their own Sentry project by setting `SENTRY_DSN` in the environment before invoking `opensre`. Leaving it unset uses the bundled default DSN. Setting `SENTRY_DSN=` (empty) drops all events at the `before_send` hook.

### Tagging deployments

Set `OPENSRE_DEPLOYMENT_METHOD` to `railway`, `langsmith`, or `local` (default `local`) to tag Sentry events with the host environment. This is a label only — it has no effect on transport or sampling.

### Inspecting outbound events

To inspect what `opensre` is sending to PostHog, every event is also appended to `~/.config/opensre/posthog_events.txt` by default. The file rotates at 1000 lines (older lines move to `posthog_events.txt.1`, overwriting any prior backup) so it never grows unbounded. To disable local logging:

```bash
export OPENSRE_ANALYTICS_LOG_EVENTS=0
```

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.

## Citations

<sup>1</sup> https://arxiv.org/abs/2310.06770

<!-- No visible change: test for post-merge PR comment workflow. -->
