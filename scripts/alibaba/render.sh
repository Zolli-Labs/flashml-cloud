#!/usr/bin/env bash
# Render the ACK overlay templates with values from .env.alibaba into
# infra/alibaba/ack/build/ (gitignored). Fails clearly on missing values.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"          # flashml-cloud
WORKSPACE="$(cd "$ROOT/.." && pwd)"
ENV_FILE="$WORKSPACE/.env.alibaba"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy .env.alibaba.example)"; exit 1; }
set -a; . "$ENV_FILE"; set +a

: "${ACR_REGISTRY:?}"; : "${ACR_NAMESPACE:?}"; : "${OSS_BUCKET:?}"; : "${OSS_ENDPOINT:?}"
: "${OSS_ACCESS_KEY_ID:?}"; : "${OSS_ACCESS_KEY_SECRET:?}"
export IMAGE_TAG="${IMAGE_TAG:?set IMAGE_TAG to an immutable tag from acr-build-push.sh}"
export OSS_SECURITY_TOKEN="${OSS_SECURITY_TOKEN:-}"
export FLASHML_RUNTIME_CLASS="${FLASHML_RUNTIME_CLASS:-}"
export FLASHML_STANDARD_NODE_SELECTOR="${FLASHML_STANDARD_NODE_SELECTOR:-}"
export FLASHML_SANDBOX_NODE_SELECTOR="${FLASHML_SANDBOX_NODE_SELECTOR:-}"
export FLASHML_SLS_ENABLED="${FLASHML_SLS_ENABLED:-false}"
export FLASHML_PROMETHEUS_ENABLED="${FLASHML_PROMETHEUS_ENABLED:-false}"

SRC="$ROOT/infra/alibaba/ack"
OUT="$SRC/build"
mkdir -p "$OUT"

# Portable ${VAR} substitution (envsubst is not present on stock macOS).
for tpl in "$SRC"/*.tpl.yaml; do
  name="$(basename "$tpl" .tpl.yaml).yaml"
  python3 -c '
import os, re, sys
text = open(sys.argv[1]).read()
sys.stdout.write(re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), text))
' "$tpl" > "$OUT/$name"
done

# The kustomization references ../../../base relative to build/.
echo "rendered ACK overlay to $OUT"
echo "review it, then: kubectl apply -k $OUT"
