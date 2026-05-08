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
[![davincios](https://avatars.githubusercontent.com/u/33206282?v=4&s=48)](https://github.com/davincios) [![VaibhavUpreti](https://avatars.githubusercontent.com/u/85568177?v=4&s=48)](https://github.com/VaibhavUpreti) [![aliya-tracer](https://avatars.githubusercontent.com/u/233726347?v=4&s=48)](https://github.com/aliya-tracer) [![arnetracer](https://avatars.githubusercontent.com/u/203629234?v=4&s=48)](https://github.com/arnetracer) [![kylie-tracer](https://avatars.githubusercontent.com/u/256781109?v=4&s=48)](https://github.com/kylie-tracer) [![0xpaulx](https://avatars.githubusercontent.com/u/214484440?v=4&s=48)](https://github.com/0xpaulx) [![zeel2104](https://avatars.githubusercontent.com/u/72783325?v=4&s=48)](https://github.com/zeel2104) [![iamkalio](https://avatars.githubusercontent.com/u/89003403?v=4&s=48)](https://github.com/iamkalio) [![w3joe](https://avatars.githubusercontent.com/u/84664178?v=4&s=48)](https://github.com/w3joe) [![yeoreums](https://avatars.githubusercontent.com/u/62932875?v=4&s=48)](https://github.com/yeoreums)
[![anandgupta1202](https://avatars.githubusercontent.com/u/39819996?v=4&s=48)](https://github.com/anandgupta1202) [![rrajan94](https://avatars.githubusercontent.com/u/25589618?v=4&s=48)](https://github.com/rrajan94) [![vrk7](https://avatars.githubusercontent.com/u/108936058?v=4&s=48)](https://github.com/vrk7) [![cerencamkiran](https://avatars.githubusercontent.com/u/150190567?v=4&s=48)](https://github.com/cerencamkiran) [![edgarmb14](https://avatars.githubusercontent.com/u/268297669?v=4&s=48)](https://github.com/edgarmb14) [![lukegimza](https://avatars.githubusercontent.com/u/68860070?v=4&s=48)](https://github.com/lukegimza) [![ebrahim-sameh](https://avatars.githubusercontent.com/u/23136098?v=4&s=48)](https://github.com/ebrahim-sameh) [![shoaib050326](https://avatars.githubusercontent.com/u/266381026?v=4&s=48)](https://github.com/shoaib050326) [![venturevd](https://avatars.githubusercontent.com/u/269883753?v=4&s=48)](https://github.com/venturevd) [![shriyashsoni](https://avatars.githubusercontent.com/u/138931443?v=4&s=48)](https://github.com/shriyashsoni)
[![Devesh36](https://avatars.githubusercontent.com/u/142524747?v=4&s=48)](https://github.com/Devesh36) [![KindaJayant](https://avatars.githubusercontent.com/u/136953152?v=4&s=48)](https://github.com/KindaJayant) [![overcastbulb](https://avatars.githubusercontent.com/u/99129410?v=4&s=48)](https://github.com/overcastbulb) [![Yashkapure06](https://avatars.githubusercontent.com/u/61585443?v=4&s=48)](https://github.com/Yashkapure06) [![Davda-James](https://avatars.githubusercontent.com/u/151067328?v=4&s=48)](https://github.com/Davda-James) [![Abhinnavverma](https://avatars.githubusercontent.com/u/138097198?v=4&s=48)](https://github.com/Abhinnavverma) [![devankitjuneja](https://avatars.githubusercontent.com/u/55021449?v=4&s=48)](https://github.com/devankitjuneja) [![ramandagar](https://avatars.githubusercontent.com/u/89700171?v=4&s=48)](https://github.com/ramandagar) [![mvanhorn](https://avatars.githubusercontent.com/u/455140?v=4&s=48)](https://github.com/mvanhorn) [![abhishek-marathe04](https://avatars.githubusercontent.com/u/175933950?v=4&s=48)](https://github.com/abhishek-marathe04)
[![yashksaini-coder](https://avatars.githubusercontent.com/u/115717039?v=4&s=48)](https://github.com/yashksaini-coder) [![haliaeetusvocifer](https://avatars.githubusercontent.com/u/20953018?v=4&s=48)](https://github.com/haliaeetusvocifer) [![Bahtya](https://avatars.githubusercontent.com/u/34988899?v=4&s=48)](https://github.com/Bahtya) [![mayankbharati-ops](https://avatars.githubusercontent.com/u/245952278?v=4&s=48)](https://github.com/mayankbharati-ops) [![harshareddy832](https://avatars.githubusercontent.com/u/53609097?v=4&s=48)](https://github.com/harshareddy832) [![sundaram2021](https://avatars.githubusercontent.com/u/93595231?v=4&s=48)](https://github.com/sundaram2021) [![micheal000010000-hub](https://avatars.githubusercontent.com/u/249460313?v=4&s=48)](https://github.com/micheal000010000-hub) [![ljivesh](https://avatars.githubusercontent.com/u/96004270?v=4&s=48)](https://github.com/ljivesh) [![gautamjain1503](https://avatars.githubusercontent.com/u/97388837?v=4&s=48)](https://github.com/gautamjain1503) [![mudittt](https://avatars.githubusercontent.com/u/96051296?v=4&s=48)](https://github.com/mudittt)
[![hamzzaaamalik](https://avatars.githubusercontent.com/u/147706212?v=4&s=48)](https://github.com/hamzzaaamalik) [![octo-patch](https://avatars.githubusercontent.com/u/266937838?v=4&s=48)](https://github.com/octo-patch) [![fuleinist](https://avatars.githubusercontent.com/u/1163738?v=4&s=48)](https://github.com/fuleinist) [![yas789](https://avatars.githubusercontent.com/u/84193712?v=4&s=48)](https://github.com/yas789) [![sharkello](https://avatars.githubusercontent.com/u/159360024?v=4&s=48)](https://github.com/sharkello) [![kaushal-bakrania](https://avatars.githubusercontent.com/u/71706867?v=4&s=48)](https://github.com/kaushal-bakrania) [![darthwade](https://avatars.githubusercontent.com/u/2220776?v=4&s=48)](https://github.com/darthwade) [![aniruddhaadak80](https://avatars.githubusercontent.com/u/127435065?v=4&s=48)](https://github.com/aniruddhaadak80) [![chaosreload](https://avatars.githubusercontent.com/u/6723037?v=4&s=48)](https://github.com/chaosreload) [![paulovitorcl](https://avatars.githubusercontent.com/u/47778440?v=4&s=48)](https://github.com/paulovitorcl)
[![gbsierra](https://avatars.githubusercontent.com/u/182822327?v=4&s=48)](https://github.com/gbsierra) [![alexanderkreidich](https://avatars.githubusercontent.com/u/126781073?v=4&s=48)](https://github.com/alexanderkreidich) [![afif1400](https://avatars.githubusercontent.com/u/51887071?v=4&s=48)](https://github.com/afif1400) [![gauravch-code](https://avatars.githubusercontent.com/u/180489802?v=4&s=48)](https://github.com/gauravch-code) [![divijgera](https://avatars.githubusercontent.com/u/46404484?v=4&s=48)](https://github.com/divijgera) [![daxp472](https://avatars.githubusercontent.com/u/177292922?v=4&s=48)](https://github.com/daxp472) [![Som-0619](https://avatars.githubusercontent.com/u/143019791?v=4&s=48)](https://github.com/Som-0619) [![Gust-svg](https://avatars.githubusercontent.com/u/265007695?v=4&s=48)](https://github.com/Gust-svg) [![Sayeem3051](https://avatars.githubusercontent.com/u/169171880?v=4&s=48)](https://github.com/Sayeem3051) [![MachineLearning-Nerd](https://avatars.githubusercontent.com/u/37579156?v=4&s=48)](https://github.com/MachineLearning-Nerd)
[![F4tal1t](https://avatars.githubusercontent.com/u/109851148?v=4&s=48)](https://github.com/F4tal1t) [![MestreY0d4-Uninter](https://avatars.githubusercontent.com/u/241404605?v=4&s=48)](https://github.com/MestreY0d4-Uninter) [![qorexdevs](https://avatars.githubusercontent.com/u/277760369?v=4&s=48)](https://github.com/qorexdevs) [![Agnuxo1](https://avatars.githubusercontent.com/u/166046035?v=4&s=48)](https://github.com/Agnuxo1) [![Ryjen1](https://avatars.githubusercontent.com/u/114498519?v=4&s=48)](https://github.com/Ryjen1) [![nandanadileep](https://avatars.githubusercontent.com/u/110280757?v=4&s=48)](https://github.com/nandanadileep) [![Maharshi-Project](https://avatars.githubusercontent.com/u/156591746?v=4&s=48)](https://github.com/Maharshi-Project) [![udit-rawat](https://avatars.githubusercontent.com/u/84604012?v=4&s=48)](https://github.com/udit-rawat) [![muddlebee](https://avatars.githubusercontent.com/u/8139783?v=4&s=48)](https://github.com/muddlebee) [![Jah-yee](https://avatars.githubusercontent.com/u/166608075?v=4&s=48)](https://github.com/Jah-yee)
[![Sarah-Salah](https://avatars.githubusercontent.com/u/11881117?v=4&s=48)](https://github.com/Sarah-Salah) [![jerome-wilson](https://avatars.githubusercontent.com/u/116165488?v=4&s=48)](https://github.com/jerome-wilson) [![hcombalicer](https://avatars.githubusercontent.com/u/40112059?v=4&s=48)](https://github.com/hcombalicer) [![CuriousHet](https://avatars.githubusercontent.com/u/102606191?v=4&s=48)](https://github.com/CuriousHet) [![Dipxssi](https://avatars.githubusercontent.com/u/151428630?v=4&s=48)](https://github.com/Dipxssi) [![sirohikartik](https://avatars.githubusercontent.com/u/99896785?v=4&s=48)](https://github.com/sirohikartik) [![imjohnzakkam](https://avatars.githubusercontent.com/u/42964266?v=4&s=48)](https://github.com/imjohnzakkam) [![paarths-collab](https://avatars.githubusercontent.com/u/205314222?v=4&s=48)](https://github.com/paarths-collab) [![wahajahmed010](https://avatars.githubusercontent.com/u/57330918?v=4&s=48)](https://github.com/wahajahmed010) [![Ade20boss](https://avatars.githubusercontent.com/u/168012500?v=4&s=48)](https://github.com/Ade20boss)
[![MichaelGurevich](https://avatars.githubusercontent.com/u/105605801?v=4&s=48)](https://github.com/MichaelGurevich) [![SB2318](https://avatars.githubusercontent.com/u/87614560?v=4&s=48)](https://github.com/SB2318) [![Davidson3556](https://avatars.githubusercontent.com/u/99369614?v=4&s=48)](https://github.com/Davidson3556) [![gitsofaryan](https://avatars.githubusercontent.com/u/117700812?v=4&s=48)](https://github.com/gitsofaryan) [![GoDiao](https://avatars.githubusercontent.com/u/104132148?v=4&s=48)](https://github.com/GoDiao) [![7vignesh](https://avatars.githubusercontent.com/u/97684755?v=4&s=48)](https://github.com/7vignesh) [![turancannb02](https://avatars.githubusercontent.com/u/131914656?v=4&s=48)](https://github.com/turancannb02) [![ShivaniNR](https://avatars.githubusercontent.com/u/47320667?v=4&s=48)](https://github.com/ShivaniNR) [![0xDevNinja](https://avatars.githubusercontent.com/u/102245100?v=4&s=48)](https://github.com/0xDevNinja) [![blut-agent](https://avatars.githubusercontent.com/u/278569635?v=4&s=48)](https://github.com/blut-agent)
[![Ghraven](https://avatars.githubusercontent.com/u/115199279?v=4&s=48)](https://github.com/Ghraven) [![kespineira](https://avatars.githubusercontent.com/u/44882187?v=4&s=48)](https://github.com/kespineira) [![AarushSharmaa](https://avatars.githubusercontent.com/u/68619452?v=4&s=48)](https://github.com/AarushSharmaa) [![Lozsku](https://avatars.githubusercontent.com/u/98460727?v=4&s=48)](https://github.com/Lozsku) [![Piyushtiwari919](https://avatars.githubusercontent.com/u/184945555?v=4&s=48)](https://github.com/Piyushtiwari919) [![hruico](https://avatars.githubusercontent.com/u/218068869?v=4&s=48)](https://github.com/hruico) [![IBOCATA](https://avatars.githubusercontent.com/u/74919012?v=4&s=48)](https://github.com/IBOCATA) [![Jeel3011](https://avatars.githubusercontent.com/u/166152117?v=4&s=48)](https://github.com/Jeel3011) [![Gingiris](https://avatars.githubusercontent.com/u/260675847?v=4&s=48)](https://github.com/Gingiris) [![rameshkumarkoyya](https://avatars.githubusercontent.com/u/109403918?v=4&s=48)](https://github.com/rameshkumarkoyya)
[![JustInCache](https://avatars.githubusercontent.com/u/105823120?v=4&s=48)](https://github.com/JustInCache) [![Genmin](https://avatars.githubusercontent.com/u/90125084?v=4&s=48)](https://github.com/Genmin) [![WatchTree-19](https://avatars.githubusercontent.com/u/119982314?v=4&s=48)](https://github.com/WatchTree-19) [![cokerrd](https://avatars.githubusercontent.com/u/82083946?v=4&s=48)](https://github.com/cokerrd) [![jason8745](https://avatars.githubusercontent.com/u/41944427?v=4&s=48)](https://github.com/jason8745) [![Yajush-afk](https://avatars.githubusercontent.com/u/180868061?v=4&s=48)](https://github.com/Yajush-afk) [![Aaryan-549](https://avatars.githubusercontent.com/u/165829168?v=4&s=48)](https://github.com/Aaryan-549) [![CoderHariswar](https://avatars.githubusercontent.com/u/113418253?v=4&s=48)](https://github.com/CoderHariswar) [![zeesshhh0](https://avatars.githubusercontent.com/u/87911619?v=4&s=48)](https://github.com/zeesshhh0) [![PrakharJain345](https://avatars.githubusercontent.com/u/171273173?v=4&s=48)](https://github.com/PrakharJain345)
[![Bhavarth7](https://avatars.githubusercontent.com/u/76651028?v=4&s=48)](https://github.com/Bhavarth7) [![emefienem](https://avatars.githubusercontent.com/u/122095740?v=4&s=48)](https://github.com/emefienem) [![TejasS1233](https://avatars.githubusercontent.com/u/145673356?v=4&s=48)](https://github.com/TejasS1233) [![DsThakurRawat](https://avatars.githubusercontent.com/u/186957976?v=4&s=48)](https://github.com/DsThakurRawat) [![akshat1074](https://avatars.githubusercontent.com/u/138868940?v=4&s=48)](https://github.com/akshat1074) [![Diwansu-pilania](https://avatars.githubusercontent.com/u/192974860?v=4&s=48)](https://github.com/Diwansu-pilania) [![AniketR10](https://avatars.githubusercontent.com/u/169879837?v=4&s=48)](https://github.com/AniketR10) [![Jai0401](https://avatars.githubusercontent.com/u/112328542?v=4&s=48)](https://github.com/Jai0401) [![shivambehl](https://avatars.githubusercontent.com/u/41379568?v=4&s=48)](https://github.com/shivambehl) [![retr0-kernel](https://avatars.githubusercontent.com/u/82054542?v=4&s=48)](https://github.com/retr0-kernel)
[![IsaacOdeimor](https://avatars.githubusercontent.com/u/218982227?v=4&s=48)](https://github.com/IsaacOdeimor) [![RajGajjar-01](https://avatars.githubusercontent.com/u/153660066?v=4&s=48)](https://github.com/RajGajjar-01) [![4arjun](https://avatars.githubusercontent.com/u/144534911?v=4&s=48)](https://github.com/4arjun) [![cloudenochcsis](https://avatars.githubusercontent.com/u/155973884?v=4&s=48)](https://github.com/cloudenochcsis) [![Thibault00](https://avatars.githubusercontent.com/u/84420566?v=4&s=48)](https://github.com/Thibault00) [![umeraamir09](https://avatars.githubusercontent.com/u/130839691?v=4&s=48)](https://github.com/umeraamir09) [![aksKrIITK](https://avatars.githubusercontent.com/u/196282905?v=4&s=48)](https://github.com/aksKrIITK) [![zerone0x](https://avatars.githubusercontent.com/u/39543393?v=4&s=48)](https://github.com/zerone0x) [![Powlisher](https://avatars.githubusercontent.com/u/200061014?v=4&s=48)](https://github.com/Powlisher) [![vidhishah2209](https://avatars.githubusercontent.com/u/179381557?v=4&s=48)](https://github.com/vidhishah2209)
[![aayushprsingh](https://avatars.githubusercontent.com/u/172073271?v=4&s=48)](https://github.com/aayushprsingh) [![shubh586](https://avatars.githubusercontent.com/u/176175004?v=4&s=48)](https://github.com/shubh586) [![mazenessam77](https://avatars.githubusercontent.com/u/184118745?v=4&s=48)](https://github.com/mazenessam77) [![mstejas610](https://avatars.githubusercontent.com/u/116860222?v=4&s=48)](https://github.com/mstejas610) [![jeetjawale](https://avatars.githubusercontent.com/u/112877983?v=4&s=48)](https://github.com/jeetjawale) [![rudra496](https://avatars.githubusercontent.com/u/78224940?v=4&s=48)](https://github.com/rudra496) [![YauhenBichel](https://avatars.githubusercontent.com/u/5603242?v=4&s=48)](https://github.com/YauhenBichel) [![thisisharsh7](https://avatars.githubusercontent.com/u/95894045?v=4&s=48)](https://github.com/thisisharsh7) [![programmerloverun](https://avatars.githubusercontent.com/u/123237285?v=4&s=48)](https://github.com/programmerloverun) [![abhinavgautam01](https://avatars.githubusercontent.com/u/183635986?v=4&s=48)](https://github.com/abhinavgautam01)
[![arian24b](https://avatars.githubusercontent.com/u/45208666?v=4&s=48)](https://github.com/arian24b) [![Dhaxor](https://avatars.githubusercontent.com/u/46064597?v=4&s=48)](https://github.com/Dhaxor) [![vivek41-glitch](https://avatars.githubusercontent.com/u/222612958?v=4&s=48)](https://github.com/vivek41-glitch) [![nawneet77](https://avatars.githubusercontent.com/u/87024784?v=4&s=48)](https://github.com/nawneet77) [![Imsharad](https://avatars.githubusercontent.com/u/19369042?v=4&s=48)](https://github.com/Imsharad) [![Mohdtalibakhtar](https://avatars.githubusercontent.com/u/66231998?v=4&s=48)](https://github.com/Mohdtalibakhtar) [![Lum1104](https://avatars.githubusercontent.com/u/87774050?v=4&s=48)](https://github.com/Lum1104) [![VibhorGautam](https://avatars.githubusercontent.com/u/55019395?v=4&s=48)](https://github.com/VibhorGautam) [![samdiano](https://avatars.githubusercontent.com/u/27571135?v=4&s=48)](https://github.com/samdiano)
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
