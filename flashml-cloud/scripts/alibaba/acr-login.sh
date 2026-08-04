#!/usr/bin/env bash
# Log docker in to Alibaba ACR. Reads .env.alibaba (or the environment).
# Prints no secrets.
set -euo pipefail

ENV_FILE="$(dirname "$0")/../../../.env.alibaba"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

: "${ACR_REGISTRY:?set ACR_REGISTRY (see: git show 244277b:.env.alibaba.example)}"
: "${ACR_USERNAME:?set ACR_USERNAME (RAM user with ACR push permission)}"
: "${ACR_PASSWORD:?set ACR_PASSWORD (not stored, not printed)}"

printf '%s' "$ACR_PASSWORD" | docker login "$ACR_REGISTRY" \
  --username "$ACR_USERNAME" --password-stdin
echo "logged in to ${ACR_REGISTRY}"
