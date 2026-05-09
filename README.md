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
- [Deployment](#deployment)
- [How OpenSRE Works](#how-opensre-works)
- [Benchmark](#benchmark)
- [Capabilities & integrations](#capabilities--integrations)
- [Contributing & development](#contributing--development)
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

Homebrew:

```bash
brew tap tracer-cloud/tap
brew install tracer-cloud/tap/opensre
```

Windows (PowerShell):

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

**Interactive shell** — with no subcommand, `opensre` starts a REPL (TTY required). Describe incidents in plain language, stream investigations, and use slash commands such as `/help`, `/status`, `/clear`, `/reset`, `/trust`, `/effort`, `/exit`. `/effort` sets reasoning depth for **OpenAI** and **Codex** providers (`low`, `medium`, `high`, `xhigh`, or `max`; other providers ignore it). Ctrl+C cancels an in-flight investigation without losing session state.

```bash
opensre
```

**One-shot investigation** — run the agent once against an alert file:

```bash
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

Other useful commands:

```bash
opensre update
opensre uninstall   # remove opensre and all local data
```

---

## Deployment

The official hosted path is **LangGraph Platform**: connect this repo, keep [`langgraph.json`](langgraph.json) at the root, and set `LLM_PROVIDER` plus the matching API key (see [`.env.example`](.env.example)). **Railway** remains supported as a self-hosted alternative; hosted Postgres and Redis (`DATABASE_URI`, `REDIS_URI`) are required for that layout.

**[Full deployment steps, Railway notes, and `opensre remote ops` → docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#deployment)**

---

## How OpenSRE Works

<img
  src="https://github.com/user-attachments/assets/936ab1f2-9bda-438d-9897-e8e9cd98e335"
  width="1064"
  height="568"
  alt="opensre-how-it-works-github"
/>

When an alert fires, OpenSRE automatically:

1. **Fetches** the alert context and correlated logs, metrics, and traces
2. **Reasons** across your connected systems to identify anomalies
3. **Generates** a structured investigation report with probable root cause
4. **Suggests** next steps and, optionally, executes remediation actions
5. **Posts** a summary directly to Slack or PagerDuty — no context switching needed

---

## Benchmark

Regenerate numbers with **`make benchmark`**; refresh this table from cached results via **`make benchmark-update-readme`**. See **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#benchmark)** for details.

<!-- BENCHMARK-START -->

_No benchmark results yet._

<!-- BENCHMARK-END -->

---

## Capabilities & integrations

|                                          |                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| 🔍 **Structured incident investigation** | Correlated root-cause analysis across all your signals                           |
| 📋 **Runbook-aware reasoning**           | OpenSRE reads your runbooks and applies them automatically                       |
| 🔮 **Predictive failure detection**      | Catch emerging issues before they page you                                       |
| 🔗 **Evidence-backed root cause**        | Every conclusion is linked to the data behind it                                 |
| 🤖 **Full LLM flexibility**              | Bring your own model — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, NVIDIA NIM |

OpenSRE connects to **60+** tools across LLMs, observability, cloud infrastructure, data platforms, incident management, and MCP. The full matrix (with roadmap links) lives in the **[product docs](https://www.opensre.com/docs)**; a detailed catalog is also maintained in-repo as the project grows.

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

OpenSRE is community-built. Good first issues: [`good first issue`](https://github.com/Tracer-Cloud/opensre/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22). See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the full workflow.

**Local environment:** **[SETUP.md](SETUP.md)** (all platforms, Windows, MCP/OpenClaw).

**Developing in this repo:** **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** (install from source, CI parity checks, dev container, benchmark, deployment detail, telemetry reference).

<p>
  <a href="https://discord.gg/7NTpevXf7w">
    <img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Join our Discord" />
  </a>
</p>

<p align="center">
  <a href="https://www.star-history.com/#Tracer-Cloud/opensre&Date">
    <img src="https://api.star-history.com/svg?repos=Tracer-Cloud/opensre&type=Date" alt="Star History Chart">
  </a>
</p>

Thanks goes to these amazing people:

<!-- readme: contributors -start -->
<a href="https://github.com/Tracer-Cloud/opensre/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Tracer-Cloud/opensre&max=200" alt="Contributors" />
</a>
<!-- readme: contributors -end -->

---

## Security

OpenSRE is designed with production environments in mind: structured and auditable LLM prompts, local transcript handling by default, and no silent bulk export of raw logs. See **[SECURITY.md](SECURITY.md)** for responsible disclosure.

---

## Telemetry

PostHog (product analytics) and Sentry (errors) are **opt-out**. Quick disable:

```bash
export OPENSRE_NO_TELEMETRY=1
```

**[Full matrix, DSN override, and local event logging → docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#telemetry-and-privacy)**

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Citations

<sup>1</sup> https://arxiv.org/abs/2310.06770
