# `e2e` MUST be here. There is a directory called e2e/, so without .PHONY make
# resolves the target to that path, finds it exists with no prerequisites, and
# reports "make: `e2e' is up to date." — running NOTHING while looking like a
# pass. That is the worst possible failure for a test target: a green that
# never executed. e2e-setup and e2e-demo do not collide with any path, which
# is why only this one was silently dead.
.PHONY: setup test check-flashml \
	e2e e2e-setup e2e-demo \
	poc-local-up poc-local-down poc-local-status poc-local-logs \
	poc-local-submit poc-local-fail-worker poc-local-forward poc-reset \
	poc-images poc-ack-bootstrap poc-acr-push poc-ack-deploy poc-ack-submit \
	poc-ack-status poc-ack-destroy

# ---------------------------------------------------------------------------
# FlashML POC — local profile (Kind + KubeRay + MinIO). See POC_PLAN.md.
# ---------------------------------------------------------------------------

KUBERAY_CHART_VERSION := 1.6.2
RAY_IMAGE             := rayproject/ray:2.46.0-py311-cpu
REGISTRY              := localhost:5001
POC_NS                := flashml
INFRA                 := flashml-cloud/infra
JOB                   ?= $$(cat /tmp/flashml-last-job-id 2>/dev/null)

# ---------------------------------------------------------------------------
# Where flashruntime and flashnode come from (changed 2026-08-01)
#
# They used to be directories in this repo. They are now the public monorepo
# github.com/Zolli-Labs/flashml, and there is exactly one copy of each.
#
# RUNTIME_VERSION / NODE_VERSION are what anything DEPLOYED or TESTED resolves.
# Keep RUNTIME_VERSION identical to the pins in
# flashml-cloud/apps/api/pyproject.toml and render.yaml — the API and the
# coordinator speaking different protocol versions is the exact failure this
# consolidation removed.
#
# FLASHML is a sibling CHECKOUT, needed only by targets that build from source
# (docker images) or that you deliberately point at local runtime edits. No
# deployed path uses it. `make check-flashml` explains what to do if missing.
# ---------------------------------------------------------------------------
FLASHML_REPO    := https://github.com/Zolli-Labs/flashml
RUNTIME_VERSION := 0.4.2
# 0.3.0 advertises local_datasets, which needs flashruntime 0.4. Keep these two
# in step: on an older runtime pydantic silently drops the field, the host
# advertises nothing, and local-data work is never placed on it — fail-closed,
# but with no error anywhere to say why.
#
# 0.3.2 raises that floor to flashruntime >=0.4.1 and the failure changes
# shape: it imports GpuInfo at module scope, so an older runtime is an
# ImportError at startup, not a silent capability gap. Nothing to notice
# late — the agent simply never registers.
#
# 0.3.3 raises it again to >=0.4.2, for the same import-time reason: the agent
# reports execution evidence at commit time and imports ExecutionEvidence at
# module scope in executor/client.py and executor/loop.py.
NODE_VERSION    := 0.3.3
FLASHML         ?= ../flashml

RUNTIME_PIN := "flashruntime[service,sklearn,dev]==$(RUNTIME_VERSION)"
NODE_PIN    := "flashnode==$(NODE_VERSION)"

check-flashml:
	@test -d "$(FLASHML)/flashruntime" || { \
		echo "error: no FlashML checkout at $(FLASHML)"; \
		echo "  This target builds from runtime/agent SOURCE, which lives in the"; \
		echo "  public monorepo, not in this repo. Clone it as a sibling:"; \
		echo ""; \
		echo "    git clone $(FLASHML_REPO).git $(FLASHML)"; \
		echo ""; \
		echo "  Or point at an existing checkout: make <target> FLASHML=/path/to/flashml"; \
		exit 1; }

poc-images: check-flashml
	cd $(FLASHML)/flashruntime && docker build -f deploy/docker/Dockerfile.kmeans  -t $(REGISTRY)/flashml/kmeans:poc-v1 .
	cd $(FLASHML)/flashruntime && docker build -f deploy/docker/Dockerfile.service -t $(REGISTRY)/flashml/runtime:poc-v1 .
	cd $(FLASHML) && docker build -f flashnode/Dockerfile -t $(REGISTRY)/flashml/node:poc-v1 .
	docker build -f flashml-cloud/apps/api/Dockerfile -t $(REGISTRY)/flashml/cloud-api:poc-v1 .
	docker pull $(RAY_IMAGE)
	docker tag  $(RAY_IMAGE) $(REGISTRY)/$(RAY_IMAGE)
	docker push $(REGISTRY)/$(RAY_IMAGE)
	docker push $(REGISTRY)/flashml/kmeans:poc-v1
	docker push $(REGISTRY)/flashml/runtime:poc-v1
	docker push $(REGISTRY)/flashml/node:poc-v1
	docker push $(REGISTRY)/flashml/cloud-api:poc-v1

poc-local-up:
	kind get clusters | grep -q '^flashml-poc$$' || \
		kind create cluster --config $(INFRA)/local/kind-cluster.yaml --wait 180s
	flashml-cloud/scripts/local/registry.sh flashml-poc
	kubectl get ns $(POC_NS) >/dev/null 2>&1 || kubectl create namespace $(POC_NS)
	helm status kuberay-operator -n $(POC_NS) >/dev/null 2>&1 || \
		helm install kuberay-operator kuberay/kuberay-operator \
			--version $(KUBERAY_CHART_VERSION) --namespace $(POC_NS)
	$(MAKE) poc-images
	kubectl apply -k $(INFRA)/local
	kubectl -n $(POC_NS) rollout status deploy/minio --timeout=180s
	kubectl -n $(POC_NS) rollout status deploy/flashruntime --timeout=180s
	kubectl -n $(POC_NS) rollout status deploy/flashml-cloud-api --timeout=180s
	kubectl -n $(POC_NS) rollout status ds/flashnode --timeout=180s
	@echo "waiting for 3 FlashNodes to register..."
	@for i in $$(seq 1 30); do \
		N=$$(kubectl -n $(POC_NS) exec deploy/flashml-cloud-api -- \
			python -c "import urllib.request,json;print(sum(1 for n in json.load(urllib.request.urlopen('http://localhost:8000/v1alpha1/nodes')) if n['online']))" 2>/dev/null || echo 0); \
		[ "$$N" = "3" ] && echo "3 FlashNodes online ✔" && exit 0; \
		sleep 2; \
	done; echo "expected 3 online FlashNodes, saw $$N" && exit 1

poc-local-submit:
	flashml-cloud/scripts/local/submit.sh

poc-local-fail-worker:
	flashml-cloud/scripts/local/fail-worker.sh

poc-local-status:
	@kubectl -n $(POC_NS) get pods -o wide
	@echo "--- rayjobs ---"
	@kubectl -n $(POC_NS) get rayjobs 2>/dev/null || true
	@if [ -n "$(JOB)" ]; then \
		echo "--- job $(JOB) ---"; \
		kubectl -n $(POC_NS) exec deploy/flashml-cloud-api -- \
			python -c "import urllib.request,json;j=json.load(urllib.request.urlopen('http://localhost:8000/v1alpha1/jobs/$(JOB)'));print(json.dumps({'state':j['state'],'runtime_execution_id':j['runtime_execution_id'],'artifacts':[a['uri'] for a in j['artifacts']]},indent=2))"; \
	fi

poc-local-logs:
	@kubectl -n $(POC_NS) logs deploy/flashruntime --tail=50
	@echo "--- cloud api ---"
	@kubectl -n $(POC_NS) logs deploy/flashml-cloud-api --tail=30
	@echo "--- flashnode (one pod) ---"
	@kubectl -n $(POC_NS) logs ds/flashnode --tail=20

# Port-forwards for browsing: cloud API :8000, runtime :8100, MinIO console :9001.
poc-local-forward:
	@echo "Ctrl-C to stop all port-forwards"
	@trap 'kill 0' INT; \
	kubectl -n $(POC_NS) port-forward svc/flashml-cloud-api 8000:8000 & \
	kubectl -n $(POC_NS) port-forward svc/flashruntime 8100:8100 & \
	kubectl -n $(POC_NS) port-forward svc/minio 9001:9001 & \
	wait

poc-local-down:
	kind delete cluster --name flashml-poc
	docker rm -f kind-registry 2>/dev/null || true

poc-reset:
	kubectl -n $(POC_NS) delete rayjobs --all 2>/dev/null || true
	kubectl -n $(POC_NS) rollout restart deploy/flashruntime deploy/flashml-cloud-api
	kubectl -n $(POC_NS) rollout restart ds/flashnode

# ---------------------------------------------------------------------------
# FlashML POC — Alibaba ACK profile. Requires .env.alibaba (see
# .env.alibaba.example) and an ACK kubeconfig context. Nothing here creates
# billable resources without an explicit confirmation.
# ---------------------------------------------------------------------------

poc-ack-bootstrap:
	@echo "This profile assumes the ACK cluster already exists (create it in"
	@echo "the console or via aliyun CLI). It will install into the CURRENT"
	@echo "kubectl context: $$(kubectl config current-context)"
	@echo "Resources to be created: namespace flashml, KubeRay operator"
	@echo "(chart $(KUBERAY_CHART_VERSION)), FlashML deployments (no ECS/OSS/ACR resources)."
	@printf "continue? [y/N] " && read a && [ "$$a" = y ]
	kubectl get ns $(POC_NS) >/dev/null 2>&1 || kubectl create namespace $(POC_NS)
	helm status kuberay-operator -n $(POC_NS) >/dev/null 2>&1 || \
		helm install kuberay-operator kuberay/kuberay-operator \
			--version $(KUBERAY_CHART_VERSION) --namespace $(POC_NS)

poc-acr-push:
	flashml-cloud/scripts/alibaba/acr-login.sh
	flashml-cloud/scripts/alibaba/acr-build-push.sh

poc-ack-deploy:
	flashml-cloud/scripts/alibaba/render.sh
	@echo "about to apply the rendered ACK overlay to context: $$(kubectl config current-context)"
	@printf "continue? [y/N] " && read a && [ "$$a" = y ]
	kubectl apply -k flashml-cloud/infra/alibaba/ack/build
	kubectl apply -f flashml-cloud/infra/alibaba/sls/aliyunlogconfig.yaml || \
		echo "SLS CRD missing — install the ACK Logtail component first (documented, non-blocking)"

poc-ack-submit:
	flashml-cloud/scripts/local/submit.sh

poc-ack-status:
	kubectl -n $(POC_NS) get pods -o wide
	kubectl -n $(POC_NS) get rayjobs 2>/dev/null || true

poc-ack-destroy:
	@echo "deletes the flashml namespace and KubeRay operator from context: $$(kubectl config current-context)"
	@printf "continue? [y/N] " && read a && [ "$$a" = y ]
	helm uninstall kuberay-operator -n $(POC_NS) || true
	kubectl delete namespace $(POC_NS)

# Set up THIS repo for local work. flashruntime and flashnode are ordinary
# pinned dependencies now — there is nothing here to build them from.
setup:
	cd flashml-cloud/apps/api && uv venv -q .venv && uv pip install -q -e ".[dev]"
	cd flashml-cloud/apps/web && npm ci

test:
	cd flashml-cloud/apps/api && .venv/bin/pytest -q
	cd flashml-cloud/apps/web && npm test

# sync-docs / check-docs are GONE (2026-08-01).
#
# They copied SYSTEM_OVERVIEW.md from flashruntime into flashnode and
# flashml-cloud, then diffed the copies to detect drift — a hand-run sync
# maintaining three copies of one document, which is the same shape of problem
# as the subtree mirroring this migration removed. flashruntime and flashnode
# now share one repo, so two of the three copies collapsed on their own; the
# copy that was in flashml-cloud/docs/ is replaced by a pointer to the
# canonical file. Nothing to sync, nothing to drift, nothing to check.

# --- end-to-end local loop (cloud-free) -------------------------------------
# Installs the PINNED runtime and agent — the same artifacts the deployed
# services resolve, not a working tree. That is what makes this suite a drift
# detector: it proves the two halves still agree at the version we ship.
#
# When you are actively changing the runtime and need the fast loop, pass a
# checkout instead:  make e2e-setup LOCAL=1
e2e-setup:
	uv venv e2e/.venv
ifdef LOCAL
	@$(MAKE) --no-print-directory check-flashml
	@echo ">>> e2e using LOCAL checkout at $(FLASHML) — not the pin. Do not trust a green run here as release evidence."
	VIRTUAL_ENV=$(CURDIR)/e2e/.venv uv pip install \
		-e "$(FLASHML)/flashruntime[service,sklearn,dev]" -e "$(FLASHML)/flashnode"
else
	VIRTUAL_ENV=$(CURDIR)/e2e/.venv uv pip install $(RUNTIME_PIN) $(NODE_PIN)
endif
	@# torch is NOT an extra of either package, and without it
	@# test_fedavg_loop.py skips at module level — silently taking the
	@# federated-averaging path (the whole point of examples/federated) out of
	@# the suite while it still reports green. This target used to omit it while
	@# the documented setup command included it, so `make e2e-setup` and the
	@# README produced different suites. They agree now; keep them that way.
	VIRTUAL_ENV=$(CURDIR)/e2e/.venv uv pip install torch numpy scikit-learn pandas scipy requests

e2e:
	e2e/.venv/bin/pytest e2e -q

e2e-demo:
	e2e/.venv/bin/python e2e/run_demo.py

# --- real second machine (LAN) ----------------------------------------------
# On THIS machine (the coordinator host):
#   make local-coordinator [JOIN_CODE=LOCAL-2026]
# On ANY OTHER machine on the LAN (needs only python3 — no repo clone now):
#   python3 -m venv .venv
#   .venv/bin/python -m pip install \
#     "flashnode @ git+https://github.com/Zolli-Labs/flashml@<pin>#subdirectory=flashnode"
#   FLASHNODE_JOIN_CODE=LOCAL-2026 .venv/bin/flashnode work --coordinator http://<this-mac-ip>:8100
#
# Once flashnode is on PyPI this collapses to `pip install flashnode`.
JOIN_CODE ?=
local-coordinator:
	@mkdir -p .local-state
	FLASHML_ENABLE_KUBERAY=0 \
	FLASHML_LEDGER_PATH=$(CURDIR)/.local-state/ledger.db \
	FLASHML_LOCAL_ARTIFACTS_DIR=$(CURDIR)/.local-state/artifacts \
	$(if $(JOIN_CODE),FLASHML_JOIN_CODE=$(JOIN_CODE)) \
	e2e/.venv/bin/python -m uvicorn flashruntime.service.app:app \
	  --host 0.0.0.0 --port 8100

local-agent:
	FLASHNODE_STATE_DIR=$(CURDIR)/.local-state/agent \
	$(if $(JOIN_CODE),FLASHNODE_JOIN_CODE=$(JOIN_CODE)) \
	e2e/.venv/bin/flashnode work --coordinator $(or $(COORDINATOR),http://localhost:8100)
