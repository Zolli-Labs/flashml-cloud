#!/usr/bin/env bash
# Failure injection: delete one *running* Ray worker pod mid-job. This is a
# genuine kill — KubeRay must recreate the pod and Ray must retry lost tasks.
set -euo pipefail

NAMESPACE=flashml

POD=$(kubectl -n "$NAMESPACE" get pods \
  -l 'ray.io/node-type=worker' \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "${POD}" ]; then
  echo "no running Ray worker pod found — submit a job first (make poc-local-submit)"
  exit 1
fi

NODE=$(kubectl -n "$NAMESPACE" get pod "$POD" -o jsonpath='{.spec.nodeName}')
echo "killing Ray worker pod: $POD (on node $NODE)"
kubectl -n "$NAMESPACE" delete pod "$POD" --wait=false
echo "deleted. KubeRay will replace it; Ray retries the lost tasks."
echo "watch recovery: kubectl -n $NAMESPACE get pods -w"
