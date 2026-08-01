#!/usr/bin/env bash
# Submit the K-Means demo job through FlashML Cloud (which delegates to
# FlashRuntime). Prints the job ID and how to watch it.
set -euo pipefail

SPEC="${1:-$(dirname "$0")/../../../flashruntime/examples/job-kmeans.yaml}"
NAMESPACE=flashml
PORT=18000

command -v python3 >/dev/null || { echo "python3 required"; exit 1; }

kubectl -n "$NAMESPACE" port-forward svc/flashml-cloud-api "$PORT:8000" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 2

JSON_SPEC=$(python3 -c "
import json, sys
try:
    import yaml
    spec = yaml.safe_load(open('$SPEC'))
except ModuleNotFoundError:
    sys.exit('pyyaml missing: run  uv pip install pyyaml  or use flashruntime CLI')
print(json.dumps(spec))
")

RESPONSE=$(curl -sf -X POST "http://localhost:$PORT/v1alpha1/jobs" \
  -H 'Content-Type: application/json' -d "$JSON_SPEC")
JOB_ID=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")

echo "submitted job: $JOB_ID"
echo "watch:   make poc-local-status JOB=$JOB_ID"
echo "events:  curl -s http://localhost:$PORT/v1alpha1/jobs/$JOB_ID/events | python3 -m json.tool"
echo "$JOB_ID" > /tmp/flashml-last-job-id
