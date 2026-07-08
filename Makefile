-include .env
export

.PHONY: install build onboard demo benchmark benchmark-update-readme \
	alert-template investigate-alert verify-integrations check-docker \
	grafana-local-up grafana-local-down grafana-local-seed \
	cloudwatch-demo datadog-demo crashloop-demo prefect-demo \
	flink-demo upstream-downstream \
	test-rca test-rca-grafana test-synthetic test-rds-synthetic test-k8s-synthetic \
	test-cloudopsbench download-cloudopsbench-hf validate-cloudopsbench \
	simulate-k8s-alert test-k8s-local test-k8s test-k8s-datadog test-k8s-eks \
	chaos-mesh-up chaos-mesh-down chaos-engineering-apply chaos-engineering-delete \
	chaos-lab-up chaos-lab-down chaos-experiment-list chaos-experiment-up chaos-experiment-down \
	deploy-dd-monitors cleanup-dd-monitors deploy-eks destroy-eks \
	trigger-alert trigger-alert-verify regen-trigger-config \
	prefect-local-test run dev docs-dev \
	build-image deploy destroy test-deploy \
	bake-gateway deploy-gateway destroy-gateway \
	deploy-gateway-direct destroy-gateway-direct \
	deploy-lambda deploy-prefect deploy-flink destroy-lambda destroy-prefect destroy-flink \
	test test-full test-cov test-scope test-cli-smoke test-turn-live test-grafana \
	rabbitmq-local-up rabbitmq-local-down test-rabbitmq-real \
	test-openclaw test-openclaw-synthetic \
	test-hermes test-hermes-synthetic test-hermes-synthetic-only refresh-hermes-tuples \
	clean lint format-check format typecheck \
	check-imports check-cycles check-layers check-imports-strict check-layers-strict check help


ifneq ($(wildcard .venv/bin/python),)
    PYTHON = .venv/bin/python
    PIP = .venv/bin/python -m pip
else ifeq ($(OS),Windows_NT)
    ifneq ($(wildcard .venv/Scripts/python.exe),)
        PYTHON = .venv/Scripts/python.exe
        PIP = .venv/Scripts/python.exe -m pip
    else
        PYTHON = python
        PIP = python -m pip
    endif
else ifneq ($(shell command -v python3 2>/dev/null),)
    PYTHON = python3
    PIP = python3 -m pip
else
    PYTHON = python
    PIP = python -m pip
endif

# PIP_INSTALL_FLAGS = --user --break-system-packages
USER_BASE := $(shell $(PYTHON) -m site --user-base)
USER_BIN := $(if $(filter Windows_NT,$(OS)),$(USER_BASE)/Scripts,$(USER_BASE)/bin)
export PATH := $(if $(wildcard .venv/bin),$(CURDIR)/.venv/bin:,$(if $(wildcard .venv/Scripts),$(CURDIR)/.venv/Scripts:))$(USER_BIN):$(PATH)

PYTHON_SOURCE_PATHS := config core gateway integrations platform surfaces tools

# Create venv and install dependencies (requires https://docs.astral.sh/uv/)
install:
	uv sync --frozen --extra dev
	uv run python -m platform.analytics.install

build:
	$(PYTHON) -m build

# Run the local onboarding flow
onboard:
	opensre onboard

# Run Prefect ECS demo (default demo) - shows Investigation Trace in RCA
demo:
	$(PYTHON) -m tests.e2e.upstream_prefect_ecs_fargate.test_agent_e2e

# Run Benchmarking Script based on Synthetic Scenarios
benchmark:
	$(PYTHON) -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator

# Update README benchmark section from cached results (no LLM calls)
benchmark-update-readme:
	$(PYTHON) -m tests.benchmarks.toolcall_model_benchmark.readme_updater

alert-template:
	opensre investigate --print-template $(or $(TEMPLATE),generic)

investigate-alert:
	@[ -n "$(ALERT)" ] || { echo "Usage: make investigate-alert ALERT=/path/to/alert.json"; exit 1; }
	opensre investigate --input "$(ALERT)"

CLOUDOPSBENCH_HF_DATASET_ID ?= tracer-cloud/cloud-ops-bench-dataset
CLOUDOPSBENCH_DATASET_DIR ?= tests/benchmarks/cloudopsbench
CLOUDOPSBENCH_BENCHMARK_DIR ?= $(CLOUDOPSBENCH_DATASET_DIR)/benchmark
CLOUDOPSBENCH_HF_INCLUDE ?= benchmark/**
CLOUDOPSBENCH_LIMIT ?=

verify-integrations:
	uv run opensre integrations verify $(if $(SERVICE),$(SERVICE),) $(if $(SLACK_TEST),--send-slack-test,)

check-docker:
	@command -v docker >/dev/null 2>&1 || { echo "Docker is required for the live local Grafana stack. Install Docker Desktop or another Docker-compatible runtime, then rerun this target."; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "Docker is installed, but the Docker daemon is not running. Start Docker Desktop, OrbStack, or Colima, then rerun this target."; exit 1; }

grafana-local-up: check-docker
	docker compose -f surfaces/cli/wizard/local_grafana_stack/docker-compose.yml up -d

grafana-local-down: check-docker
	docker compose -f surfaces/cli/wizard/local_grafana_stack/docker-compose.yml down

grafana-local-seed:
	$(PYTHON) -m surfaces.cli.wizard.grafana_seed

# Run CloudWatch demo
cloudwatch-demo:
	$(PYTHON) -m tests.e2e.cloudwatch_demo.test_aws

# Run Datadog demo (local kind cluster + real DD monitor + investigation agent)
datadog-demo:
	$(PYTHON) -m tests.e2e.datadog.test_local

# Run CrashLoopBackOff  demo
crashloop-demo:
	$(PYTHON) -m tests.e2e.crashloop.test_local

# Run Prefect ECS Fargate E2E test (alias for demo)
prefect-demo:
	$(PYTHON) -m tests.e2e.upstream_prefect_ecs_fargate.test_agent_e2e

# Run RCA tests from markdown alert files in tests/e2e/rca/ (pass FILE= to run one)
test-rca:
	$(PYTHON) -m tests.e2e.rca.run_rca_test $(FILE)

# Run synthetic tests via pytest markers (fixture-based, no live infra required)
test-synthetic:
	$(PYTHON) -m pytest -m synthetic -v tests/synthetic/

# Run synthetic RDS PostgreSQL RCA benchmark suite via the CLI runner (supports --json, --scenario)
test-rds-synthetic:
	$(PYTHON) -m tests.synthetic.rds_postgres.run_suite $(if $(SCENARIO),--scenario $(SCENARIO),)

# Run synthetic Kubernetes RCA benchmark suite via the CLI runner (supports --json, --scenario, --mock-backends)
test-k8s-synthetic:
	$(PYTHON) -m tests.synthetic.eks.run_suite $(if $(SCENARIO),--scenario $(SCENARIO),)

# Run Cloud-OpsBench RCA benchmark suite via the OpenSRE runner
test-cloudopsbench:
	$(PYTHON) -m tests.benchmarks.cloudopsbench.run_suite --benchmark-dir "$(CLOUDOPSBENCH_BENCHMARK_DIR)" $(if $(SYSTEM),--system $(SYSTEM),) $(if $(FAULT),--fault-category $(FAULT),) $(if $(CASE),--case $(CASE),) $(if $(CLOUDOPSBENCH_LIMIT),--limit $(CLOUDOPSBENCH_LIMIT),$(if $(LIMIT),--limit $(LIMIT),))

# Download Cloud-OpsBench benchmark data from Hugging Face.
download-cloudopsbench-hf:
	@command -v hf >/dev/null 2>&1 || { echo "Install the Hugging Face CLI with: pip install 'huggingface_hub[cli]'"; exit 1; }
	hf download "$(CLOUDOPSBENCH_HF_DATASET_ID)" --repo-type dataset --local-dir "$(CLOUDOPSBENCH_DATASET_DIR)" --include "$(CLOUDOPSBENCH_HF_INCLUDE)"

validate-cloudopsbench:
	$(PYTHON) -m tests.benchmarks.cloudopsbench.run_suite --benchmark-dir "$(CLOUDOPSBENCH_BENCHMARK_DIR)" --validate-only

# Boot local Grafana+Loki, seed deterministic test logs, then run the RCA pipeline
# Requires GRAFANA_INSTANCE_URL + GRAFANA_READ_TOKEN in .env (see .env.example for local defaults)
test-rca-grafana: grafana-local-up grafana-local-seed
	$(PYTHON) -m tests.e2e.rca.run_rca_test grafana_pipeline_failure

# Run Kubernetes local alert simulation against the in-process investigation API
simulate-k8s-alert:
	$(PYTHON) -m pytest tests/e2e/kubernetes_local_alert_simulation/test_simulation.py -s; \
	EXIT=$$?; exit $$EXIT

# Run Kubernetes local test (kind)
test-k8s-local:
	$(PYTHON) -m tests.e2e.kubernetes.test_local --both

# Run Kubernetes test (matches CI)
test-k8s:
	$(PYTHON) -m tests.e2e.kubernetes.test_local

# Run Kubernetes + Datadog test (kind + DD Agent)
test-k8s-datadog:
	$(PYTHON) -m tests.e2e.kubernetes.test_datadog

# Chaos Mesh on the kube context (default: kind-tracer-k8s-test). Override: make chaos-mesh-up KUBECTL_CONTEXT=...
# CHAOS_MESH_RUNTIME=containerd matches kind; use docker only on older clusters.
CHAOS_MESH_NS ?= chaos-mesh
KUBECTL_CONTEXT ?= kind-tracer-k8s-test
CHAOS_MESH_RUNTIME ?= containerd
HELM_KUBE := $(if $(KUBECTL_CONTEXT),--kube-context $(KUBECTL_CONTEXT),)
KUBECTL_FLAGS := $(if $(KUBECTL_CONTEXT),--context=$(KUBECTL_CONTEXT),)

chaos-mesh-up:
	@helm repo list 2>/dev/null | grep -q '^chaos-mesh' || helm repo add chaos-mesh https://charts.chaos-mesh.org
	helm repo update
	kubectl create namespace $(CHAOS_MESH_NS) --dry-run=client -o yaml | kubectl apply -f - $(KUBECTL_FLAGS)
	helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh -n $(CHAOS_MESH_NS) \
		--set chaosDaemon.runtime=$(CHAOS_MESH_RUNTIME) \
		$(HELM_KUBE)

chaos-mesh-down:
	-helm uninstall chaos-mesh -n $(CHAOS_MESH_NS) $(HELM_KUBE)
	-kubectl delete namespace $(CHAOS_MESH_NS) $(KUBECTL_FLAGS)

# Apply chaos-engineering manifests on KUBECTL_CONTEXT (nginx target, CrashLoop deployment, PodChaos).
# Requires Chaos Mesh CRDs for pod-kill-demo.yaml (run make chaos-mesh-up first).
chaos-engineering-apply:
	kubectl apply -f tests/chaos_engineering/chaos-demo.yaml $(KUBECTL_FLAGS)
	kubectl apply -f tests/chaos_engineering/experiments/crashloop/crashloop-demo.yaml $(KUBECTL_FLAGS)
	kubectl apply -f tests/chaos_engineering/pod-kill-demo.yaml $(KUBECTL_FLAGS)

chaos-engineering-delete:
	-kubectl delete -f tests/chaos_engineering/pod-kill-demo.yaml $(KUBECTL_FLAGS)
	-kubectl delete -f tests/chaos_engineering/experiments/crashloop/crashloop-demo.yaml $(KUBECTL_FLAGS)
	-kubectl delete -f tests/chaos_engineering/chaos-demo.yaml $(KUBECTL_FLAGS)

# Full chaos lab: kind + Datadog + Chaos Mesh + baseline workloads (same defaults as README).
# Optional flags: CHAOS_LAB_FLAGS='--skip-kind' '--skip-datadog' '--no-wait-datadog' etc.
chaos-lab-up:
	$(PYTHON) -m tests.chaos_engineering lab up $(CHAOS_LAB_FLAGS)

# Tear down lab (baseline, Chaos Mesh, Datadog namespace, kind cluster). Optional: CHAOS_LAB_DOWN_FLAGS='--keep-kind' '--keep-datadog'
chaos-lab-down:
	$(PYTHON) -m tests.chaos_engineering lab down $(CHAOS_LAB_DOWN_FLAGS)

chaos-experiment-list:
	$(PYTHON) -m tests.chaos_engineering experiment list

# Apply experiments/<EXPERIMENT>/ (*-demo.yaml then *-chaos.yaml). Example: make chaos-experiment-up EXPERIMENT=pod-failure
chaos-experiment-up:
	@test -n "$(EXPERIMENT)" || (echo "Set EXPERIMENT=name (see: make chaos-experiment-list)" && false)
	$(PYTHON) -m tests.chaos_engineering experiment apply $(EXPERIMENT)

chaos-experiment-down:
	@test -n "$(EXPERIMENT)" || (echo "Set EXPERIMENT=name (see: make chaos-experiment-list)" && false)
	$(PYTHON) -m tests.chaos_engineering experiment delete $(EXPERIMENT)

# Deploy Datadog monitors (requires DD_API_KEY + DD_APP_KEY)
deploy-dd-monitors:
	$(PYTHON) -c "from tests.e2e.kubernetes.test_datadog import deploy_monitors; deploy_monitors()"

# Remove Datadog monitors created by tracer tests
cleanup-dd-monitors:
	$(PYTHON) -c "from tests.e2e.kubernetes.test_datadog import cleanup_monitors; cleanup_monitors()"

# Deploy EKS cluster + ECR image for Kubernetes tests
deploy-eks:
	$(PYTHON) -c "from tests.e2e.kubernetes.infrastructure_sdk.eks import deploy_eks_stack; deploy_eks_stack()"

# Destroy EKS cluster and all associated resources
destroy-eks:
	$(PYTHON) -c "from tests.e2e.kubernetes.infrastructure_sdk.eks import destroy_eks_stack; destroy_eks_stack()"

# Run Kubernetes + Datadog test on EKS
test-k8s-eks:
	$(PYTHON) -m tests.e2e.kubernetes.test_eks

# Fast: trigger a K8s alert in ~15s (fire-and-forget)
trigger-alert:
	$(PYTHON) -m tests.e2e.kubernetes.trigger_alert

# Recreate centralized trigger API config JSON from AWS
regen-trigger-config:
	$(PYTHON) -m tests.e2e.kubernetes.trigger_alert --regen-config

# Fast trigger + wait for Slack confirmation
trigger-alert-verify:
	$(PYTHON) -m tests.e2e.kubernetes.trigger_alert --verify

# Run Prefect ECS local test
prefect-local-test:
	$(PYTHON) -m tests.e2e.upstream_prefect_ecs_fargate.test_local $(if $(CLOUD),--cloud,)

# Run upstream/downstream pipeline E2E test
upstream-downstream:
	$(PYTHON) -m tests.e2e.upstream_lambda.test_agent_e2e

# Run Apache Flink ECS E2E test
flink-demo:
	$(PYTHON) -m tests.e2e.upstream_apache_flink_ecs.test_agent_e2e

# Run the generic CLI (reads from stdin or --input)
run:
	opensre investigate

dev:
	@echo "Run the health app with: uv run uvicorn gateway.webapp:app --reload --host 0.0.0.0 --port 8000"

docs-dev:
	cd docs && mint dev


# Deploy all test case infrastructure in parallel (SDK - fast!)
# EC2 deploy (web + gateway containers on one instance)
# Step 1 — build once per code change, saves URI locally for reuse:
build-image:
	$(PYTHON) -m platform.deployment.ecr_deploy.lifecycle build-image

# Step 2 — launch instance using the pre-built image (fast, no Docker build):
deploy:
	$(PYTHON) -m platform.deployment.ecr_deploy.lifecycle deploy

destroy:
	$(PYTHON) -m platform.deployment.ecr_deploy.lifecycle destroy

test-deploy:
	$(PYTHON) -m pytest tests/deployment/ec2/ -v -s

# Gateway deploy (Telegram gateway only, no Docker/ECR)
# Step 1 — bake once per code change (launches temp EC2, installs opensre, snapshots AMI):
bake-gateway:
	$(PYTHON) -m platform.deployment.gateway.lifecycle bake-ami

# Step 2 — launch gateway instance from pre-baked AMI (fast):
deploy-gateway:
	$(PYTHON) -m platform.deployment.gateway.lifecycle deploy

destroy-gateway:
	$(PYTHON) -m platform.deployment.gateway.lifecycle destroy

# Gateway direct deploy (no pre-baked AMI — installs inline via SSM)
deploy-gateway-direct:
	$(PYTHON) -m platform.deployment.gateway.lifecycle deploy-direct

destroy-gateway-direct:
	$(PYTHON) -m platform.deployment.gateway.lifecycle destroy-direct

# Deploy Lambda test case
deploy-lambda:
	@echo "Deploying Lambda stack..."
	$(PYTHON) -m tests.e2e.upstream_lambda.infrastructure_sdk.deploy

# Deploy Prefect ECS test case
deploy-prefect:
	@echo "Deploying Prefect ECS stack..."
	$(PYTHON) -m tests.e2e.upstream_prefect_ecs_fargate.infrastructure_sdk.deploy

# Deploy Flink ECS test case
deploy-flink:
	@echo "Deploying Flink ECS stack..."
	$(PYTHON) -m tests.e2e.upstream_apache_flink_ecs.infrastructure_sdk.deploy

# Destroy Lambda test case
destroy-lambda:
	@echo "Destroying Lambda stack..."
	$(PYTHON) -m tests.e2e.upstream_lambda.infrastructure_sdk.destroy

# Destroy Prefect ECS test case
destroy-prefect:
	@echo "Destroying Prefect ECS stack..."
	$(PYTHON) -m tests.e2e.upstream_prefect_ecs_fargate.infrastructure_sdk.destroy

# Destroy Flink ECS test case
destroy-flink:
	@echo "Destroying Flink ECS stack..."
	$(PYTHON) -m tests.e2e.upstream_apache_flink_ecs.infrastructure_sdk.destroy

# Run fast tests + Prefect cloud E2E
test:
	$(PYTHON) -m pytest -v surfaces/cli tests/utils
	$(PYTHON) -m tests.e2e.upstream_prefect_ecs_fargate.test_agent_e2e

# Run full test suite (CI/CD)
test-full:
	$(PYTHON) -m pytest -v

# Run tests with coverage (parallel via pytest-xdist).
# Keep tests/synthetic excluded here to match GitHub CI; marker filtering alone is
# not enough because some synthetic tests are collected without the synthetic mark.
test-cov:
	$(PYTHON) -m pytest -n auto -v $(addprefix --cov=,$(PYTHON_SOURCE_PATHS)) --cov-report=term-missing --ignore=tests/e2e/kubernetes_local_alert_simulation --ignore=tests/synthetic -m "not synthetic"

# Run only the tests relevant to files changed on this branch (local use only).
# Pass ARGS=--dry-run to preview the command without executing it.
test-scope:
	$(PYTHON) .github/ci/run_test_scope.py --base main $(ARGS)

# Run the CLI smoke suite against the installed opensre entrypoint.
test-cli-smoke:
	$(PYTHON) -m pytest -v tests/cli_smoke_test.py

# Run the live-LLM turn scenario suite sharded across local processes, mirroring
# the CI turn-live job. The suite is IO-bound on LLM calls, so running all shards
# concurrently collapses wall time to ~one shard. Override shard count/subset:
#   make test-turn-live ARGS="--shards 4"
#   make test-turn-live ARGS="--indexes 0,3"
test-turn-live:
	$(PYTHON) .github/ci/run_live_turn_shards.py $(ARGS)

# Run Grafana integration tests
test-grafana:
	@echo "Running Grafana integration tests..."
	$(PYTHON) -m pytest tests/e2e/grafana_validation/test_grafana_cloud_queries.py -v

# Spin up the local RabbitMQ stack (broker + publisher + slow consumer), wait
# for a backlog to accumulate, then exercise the read-only diagnostic tools
# against the real broker.  Used for the screen-video demo; NOT part of CI.
RABBITMQ_COMPOSE = tests/e2e/rabbitmq/docker-compose.yml

rabbitmq-local-up:
	@echo "Starting local RabbitMQ stack (broker + publisher + slow consumer)..."
	docker compose -f $(RABBITMQ_COMPOSE) up -d
	@echo "Waiting for broker to become healthy..."
	@until docker compose -f $(RABBITMQ_COMPOSE) ps rabbitmq | grep -q "(healthy)"; do sleep 2; done
	@echo "Broker healthy.  Letting backlog build for 20s..."
	@sleep 20
	@echo "Ready."

rabbitmq-local-down:
	docker compose -f $(RABBITMQ_COMPOSE) down -v

# Run OpenClaw integration + tool E2E tests (mocked transport, no live OpenClaw needed)
test-openclaw:
	$(PYTHON) -m pytest tests/e2e/openclaw/ tests/test_openclaw_integration.py tests/tools/test_openclaw_mcp_tool.py tests/utils/test_openclaw_delivery.py -v

# Run synthetic OpenClaw investigation scenarios (FixtureOpenClawBackend, no live OpenClaw)
test-openclaw-synthetic:
	$(PYTHON) -m tests.synthetic.openclaw.run_suite

# Run Hermes incident-identification suites: Hermes RCA synthetic tests + Hermes e2e.
test-hermes:
	$(PYTHON) -m pytest tests/synthetic/hermes_rca tests/e2e/hermes -v

# Deterministic/no-key Hermes RCA synthetic checks only.
test-hermes-synthetic:
	$(PYTHON) -m pytest tests/synthetic/hermes_rca -v

# Offline-only Hermes synthetic runner (scenario harness path).
test-hermes-synthetic-only:
	$(PYTHON) -m tests.synthetic.hermes_rca.run_suite --offline-only

# Regenerate Hermes adapter tuple catalog.
refresh-hermes-tuples:
	$(PYTHON) -m tests.synthetic.hermes_rca.refresh_adapter_tuples

# Run the RabbitMQ integration + tool tests, then invoke the verify command
# against the live broker.  Requires the rabbitmq-local-up stack to be running.
test-rabbitmq-real:
	@echo "Running mocked RabbitMQ unit + e2e tests..."
	$(PYTHON) -m pytest tests/integrations/test_rabbitmq.py tests/tools/test_rabbitmq_*.py tests/e2e/rabbitmq/ -v
	@echo ""
	@echo "Verifying against the live broker (requires \`make rabbitmq-local-up\`)..."
	RABBITMQ_HOST=127.0.0.1 \
	RABBITMQ_USERNAME=sre_admin \
	RABBITMQ_PASSWORD=sre_password \
	RABBITMQ_VHOST=/orders \
	$(PYTHON) -c "from integrations.rabbitmq import rabbitmq_config_from_env, validate_rabbitmq_config, get_queue_backlog, get_broker_overview; \
cfg = rabbitmq_config_from_env(); \
print('validate:', validate_rabbitmq_config(cfg)); \
print('overview:', get_broker_overview(cfg)); \
print('backlog:', get_queue_backlog(cfg, max_results=5))"

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -maxdepth 1 \( -name '.coverage' -o -name '.coverage.*' \) -delete 2>/dev/null || true
	rm -rf htmlcov/ 2>/dev/null || true

# Lint code
lint:
	$(PYTHON) -m ruff check $(PYTHON_SOURCE_PATHS) tests/

# Check formatting (read-only; CI uses this)
format-check:
	$(PYTHON) -m ruff format --check $(PYTHON_SOURCE_PATHS) tests/

# Format code
format:
	$(PYTHON) -m ruff format $(PYTHON_SOURCE_PATHS) tests/

# Type check
typecheck:
	$(PYTHON) -m mypy $(PYTHON_SOURCE_PATHS)

# Import graph: cycles + layering + forbidden direct edges (one command).
check-imports:
	$(PYTHON) .github/ci/check_imports.py

# Deprecated aliases — use ``check-imports`` instead.
check-cycles check-layers: check-imports

# Optional: full transitive layer contracts (when .importlinter.strict exists).
check-imports-strict:
	$(PYTHON) .github/ci/check_imports.py --strict

check-layers-strict: check-imports-strict

# Run all checks (lint + format read-only check + types + imports + full tests; mirrors CI quality gates)
check: lint format-check typecheck check-imports test-full

# Show help
help:
	@echo "Available commands:"
	@echo ""
	@echo "  EC2 DEPLOY (Docker/ECR — web + gateway)"
	@echo "  make build-image       - Build and push Docker image to ECR (run once per code change)"
	@echo "  make deploy            - Launch EC2 instance using pre-built image (fast, no Docker build)"
	@echo "  make destroy           - Terminate EC2 instance and clean up (keeps ECR image; OPENSRE_DESTROY_PURGE_ECR=1 to also delete it)"
	@echo "  make test-deploy       - Run EC2 deployment e2e tests"
	@echo ""
	@echo "  GATEWAY DEPLOY (systemd, no Docker — gateway only)"
	@echo "  make bake-gateway    - Bake a gateway AMI (run once per code change; saves AMI id locally)"
	@echo "  make deploy-gateway  - Launch gateway EC2 instance from pre-baked AMI (fast)"
	@echo "  make destroy-gateway - Terminate gateway instance and clean up (set OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1 to also deregister AMI)"
	@echo ""
	@echo "  E2E TEST INFRA (AWS SDK)"
	@echo "  make deploy-lambda     - Deploy Lambda stack (~50s)"
	@echo "  make deploy-prefect    - Deploy Prefect ECS stack (~55s)"
	@echo "  make deploy-flink      - Deploy Flink ECS stack (~90s)"
	@echo "  make destroy-lambda    - Destroy Lambda stack"
	@echo "  make destroy-prefect   - Destroy Prefect ECS stack"
	@echo "  make destroy-flink     - Destroy Flink ECS stack"
	@echo ""
	@echo "  DEMOS"
	@echo "  make demo            - Run Prefect ECS E2E test (default, shows Investigation Trace)"
	@echo "  make grafana-local-up - Start the local Grafana + Loki stack"
	@echo "  make grafana-local-seed - Seed failure logs into the local Loki instance"
	@echo "  make alert-template TEMPLATE=datadog - Print a starter alert JSON template"
	@echo "  make investigate-alert ALERT=/path/to/alert.json - Run RCA against your own alert payload"
	@echo "  make verify-integrations - Check local store + .env integrations before running RCA"
	@echo "  make prefect-demo    - Run Prefect ECS Fargate E2E test (alias for demo)"
	@echo "  make prefect-local-test - Run Prefect ECS local test (CLOUD=1 for ECS)"
	@echo "  make flink-demo      - Run Apache Flink ECS E2E test"
	@echo "  make cloudwatch-demo - Run CloudWatch demo"
	@echo "  make datadog-demo    - Run Datadog demo (local kind cluster + DD monitor + agent)"
	@echo "  make crashloop-demo  - Run CrashLoopBackOff/OOMKill demo (no k8s needed, DD + Slack)"
	@echo "  make upstream-downstream - Run upstream/downstream Lambda E2E test"
	@echo ""
	@echo "  KUBERNETES"
	@echo "  make test-k8s-local  - Run Kubernetes local test (kind)"
	@echo "  make test-k8s        - Run Kubernetes test (matches CI)"
	@echo "  make test-k8s-datadog - Run Kubernetes + Datadog test"
	@echo "  make chaos-mesh-up - Install Chaos Mesh (Helm; default context kind-tracer-k8s-test)"
	@echo "  make chaos-mesh-down - Uninstall Chaos Mesh + namespace"
	@echo "  make chaos-engineering-apply - Apply chaos-demo + crashloop + PodChaos (same context)"
	@echo "  make chaos-engineering-delete - Remove those workloads (PodChaos first)"
	@echo "  make chaos-lab-up / chaos-lab-down - Full lab (kind+DD+mesh+baseline; runs python -m tests.chaos_engineering)"
	@echo "  make chaos-experiment-list / chaos-experiment-up EXPERIMENT=... - Per-experiment apply"
	@echo "  make deploy-dd-monitors - Deploy Datadog monitors (DD_API_KEY + DD_APP_KEY)"
	@echo "  make cleanup-dd-monitors - Remove Datadog test monitors"
	@echo "  make deploy-eks      - Deploy EKS cluster + ECR image"
	@echo "  make destroy-eks     - Destroy EKS cluster and resources"
	@echo "  make test-k8s-eks    - Run Kubernetes + Datadog test on EKS"
	@echo ""
	@echo "  LOCAL DEVELOPMENT"
	@echo "  make install         - Install dependencies"
	@echo "  make onboard         - Run the OpenSRE onboarding flow"
	@echo "  make docs-dev        - Start the local documentation preview (requires mint CLI)"
	@echo ""
	@echo "  CLI (tab-completable, run 'opensre -h' for full help)"
	@echo "  opensre onboard                    - Interactive setup wizard"
	@echo "  opensre investigate -i alert.json  - Run RCA on an alert payload"
	@echo "  opensre integrations list          - Show configured integrations"
	@echo "  opensre integrations verify        - Verify connectivity"
	@echo ""
	@echo "  TESTING & QUALITY"
	@echo "  make test            - Run fast unit tests + Prefect cloud E2E"
	@echo "  make test-full       - Run full test suite (CI/CD)"
	@echo "  make test-cov        - Run tests with coverage"
	@echo "  make test-cli-smoke  - Run end-to-end CLI smoke tests"
	@echo "  make test-grafana    - Run Grafana integration tests"
	@echo "  make test-rca        - Run all RCA markdown alert tests in tests/e2e/rca/"
	@echo "  make test-rca FILE=pipeline_error_in_logs - Run a single RCA alert test"
	@echo "  make test-rds-synthetic - Run the synthetic RDS PostgreSQL RCA suite"
	@echo "  make test-openclaw   - Run OpenClaw integration + e2e tests (skips when openclaw CLI absent)"
	@echo "  make test-hermes     - Run Hermes synthetic + e2e suites"
	@echo "  make test-hermes-synthetic - Run Hermes RCA synthetic suite only (no-key deterministic path)"
	@echo "  make download-cloudopsbench-hf - Download Cloud-OpsBench from Hugging Face"
	@echo "  make test-cloudopsbench - Run the Cloud-OpsBench synthetic RCA suite"
	@echo "  make clean           - Clean up cache files"
	@echo "  make lint            - Lint code with ruff"
	@echo "  make format-check    - Check formatting with ruff (read-only)"
	@echo "  make format          - Format code with ruff"
	@echo "  make typecheck       - Type check with mypy"
	@echo "  make check-imports   - Import cycles, layers, and direct-edge checks"
	@echo "  make check-layers-strict - Full transitive layer contracts (.importlinter.strict)"
	@echo "  make check           - Run all checks"
	@echo "  make benchmark		  - Run benchmark report generation"
	@echo "  make benchmark-update-readme - Update README from cached benchmark results"
