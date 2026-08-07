#!/usr/bin/env bash
# Build all FlashML images and push them to ACR with an immutable tag.
# Usage: acr-build-push.sh [tag]   (default tag: poc-v1-<short git sha of flashruntime>)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV_FILE="$ROOT/.env.alibaba"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

: "${ACR_REGISTRY:?set ACR_REGISTRY (see: git show 244277b:.env.alibaba.example)}"
: "${ACR_NAMESPACE:?set ACR_NAMESPACE}"

SHA=$(git -C "$ROOT/flashruntime" rev-parse --short HEAD 2>/dev/null || echo dev)
TAG="${1:-poc-v1-${SHA}}"
PREFIX="${ACR_REGISTRY}/${ACR_NAMESPACE}"

build() { docker build -f "$1" -t "$2" "$3"; }

build "$ROOT/flashruntime/deploy/docker/Dockerfile.kmeans"  "${PREFIX}/kmeans:${TAG}"     "$ROOT/flashruntime"
build "$ROOT/flashruntime/deploy/docker/Dockerfile.service" "${PREFIX}/runtime:${TAG}"    "$ROOT/flashruntime"
build "$ROOT/flashnode/Dockerfile"                   "${PREFIX}/node:${TAG}"       "$ROOT"
build "$ROOT/flashml-cloud/apps/api/Dockerfile"      "${PREFIX}/cloud-api:${TAG}"  "$ROOT"

for image in kmeans runtime node cloud-api; do
  docker push "${PREFIX}/${image}:${TAG}"
done

echo
echo "pushed image references (use these exact tags in the ACK overlay):"
for image in kmeans runtime node cloud-api; do
  echo "  ${PREFIX}/${image}:${TAG}"
done
