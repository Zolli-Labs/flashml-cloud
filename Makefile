.PHONY: setup test sync-docs check-docs \
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

poc-images:
	cd flashruntime && docker build -f deploy/docker/Dockerfile.kmeans  -t $(REGISTRY)/flashml/kmeans:poc-v1 .
	cd flashruntime && docker build -f deploy/docker/Dockerfile.service -t $(REGISTRY)/flashml/runtime:poc-v1 .
	docker build -f flashnode/Dockerfile          -t $(REGISTRY)/flashml/node:poc-v1 .
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

setup:
	cd flashruntime && uv venv -q && uv pip install -q -e ".[sklearn,dev]"
	cd flashnode && uv venv -q && uv pip install -q -e ".[dev]"

test:
	cd flashruntime && .venv/bin/pytest -q

# SYSTEM_OVERVIEW.md is canonical in flashruntime; the other repos carry
# synced copies. Edit flashruntime/docs/SYSTEM_OVERVIEW.md, then run this.
sync-docs:
	cp flashruntime/docs/SYSTEM_OVERVIEW.md flashnode/docs/SYSTEM_OVERVIEW.md
	cp flashruntime/docs/SYSTEM_OVERVIEW.md flashml-cloud/docs/SYSTEM_OVERVIEW.md
	@echo "Synced. Remember to commit in flashnode and flashml-cloud too."

check-docs:
	@diff -q flashruntime/docs/SYSTEM_OVERVIEW.md flashnode/docs/SYSTEM_OVERVIEW.md >/dev/null \
		&& diff -q flashruntime/docs/SYSTEM_OVERVIEW.md flashml-cloud/docs/SYSTEM_OVERVIEW.md >/dev/null \
		&& echo "SYSTEM_OVERVIEW.md in sync across all repos" \
		|| { echo "DRIFT DETECTED: run 'make sync-docs' (after confirming flashruntime's copy is the intended one)"; exit 1; }

# --- end-to-end local loop (cloud-free) -------------------------------------
e2e-setup:
	uv venv e2e/.venv
	VIRTUAL_ENV=$(CURDIR)/e2e/.venv uv pip install -e "./flashruntime[service,sklearn,dev]" -e ./flashnode

e2e:
	e2e/.venv/bin/pytest e2e -q

e2e-demo:
	e2e/.venv/bin/python e2e/run_demo.py

# --- real second machine (LAN) ----------------------------------------------
# On THIS machine (the coordinator host):
#   make local-coordinator [JOIN_CODE=LOCAL-2026]
# On ANY OTHER machine on the LAN (needs: uv, git clone of the two repos):
#   uv venv .venv && uv pip install -e ./flashruntime -e ./flashnode
#   FLASHNODE_JOIN_CODE=LOCAL-2026 .venv/bin/flashnode work --coordinator http://<this-mac-ip>:8100
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
