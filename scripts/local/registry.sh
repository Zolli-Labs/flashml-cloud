#!/usr/bin/env bash
# Local image registry for the Kind POC cluster (kind.sigs.k8s.io/docs/user/local-registry).
# Why a registry instead of `kind load`: Docker 29's containerd image store
# exports multi-arch manifest indexes that reference platforms it doesn't
# hold, which breaks `ctr images import` on the nodes. Pulling from a local
# registry sidesteps that and mirrors the ACR flow on Alibaba.
set -euo pipefail

REG_NAME="kind-registry"
REG_PORT="5001"
CLUSTER_NAME="${1:-flashml-poc}"

# 1. Registry container (idempotent).
if [ "$(docker inspect -f '{{.State.Running}}' "${REG_NAME}" 2>/dev/null || true)" != 'true' ]; then
  docker rm -f "${REG_NAME}" 2>/dev/null || true
  docker run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" \
    --name "${REG_NAME}" registry:3
fi

# 2. Tell every node to resolve localhost:5001 to the registry container.
REGISTRY_DIR="/etc/containerd/certs.d/localhost:${REG_PORT}"
for node in $(kind get nodes --name "${CLUSTER_NAME}"); do
  docker exec "${node}" mkdir -p "${REGISTRY_DIR}"
  cat <<EOF | docker exec -i "${node}" cp /dev/stdin "${REGISTRY_DIR}/hosts.toml"
[host."http://${REG_NAME}:5000"]
EOF
done

# 3. Put the registry on the cluster network.
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "${REG_NAME}")" = 'null' ]; then
  docker network connect "kind" "${REG_NAME}"
fi

# 4. Document the registry for cluster tooling.
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REG_PORT}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
EOF

echo "local registry ready: localhost:${REG_PORT}"
