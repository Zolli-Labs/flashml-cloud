# Alibaba ACK deployment profile

Same manifests, protocol, and images as the local profile — different
substrate. Nothing here was invented for the demo: the local Kind profile
and this profile share `infra/base/` and the FlashRuntime job model.

Status honesty: this profile is **configured and rendered, deployed only
when credentials are available**. Do not claim it was verified unless the
smoke test below actually ran against a cluster.

## Prerequisites

- An ACK managed cluster (`ACK_CLUSTER_NAME`), kubeconfig context active.
- KubeRay operator installed (same pinned chart 1.6.2):
  `helm install kuberay-operator kuberay/kuberay-operator --version 1.6.2 -n flashml`
  or the ACK application-catalog equivalent.
- ACR namespace, OSS bucket, RAM user(s) with least privilege:
  - ACR: push/pull on the `${ACR_NAMESPACE}` namespace only;
  - OSS: read/write on `${OSS_BUCKET}` only (prefer STS / RRSA over
    long-lived keys).
- `.env.alibaba` at the workspace root (copy `.env.alibaba.example`).

## Deploy

```bash
make poc-acr-push        # builds + pushes immutable tags, prints references
export IMAGE_TAG=<tag printed above>
make poc-ack-deploy      # renders infra/alibaba/ack/build/ and applies it
make poc-ack-submit      # same JobSpec as local
make poc-ack-status
```

`make poc-ack-deploy` prints the resource list and asks for confirmation
before creating anything.

## Per-service notes

- **ACR** (`../..//scripts/alibaba/acr-*.sh`): env-driven, no secrets
  printed, immutable tags only (`poc-v1-<git sha>`), fails clearly when
  credentials are absent.
- **OSS** (`ack/secret-oss.tpl.yaml` + `OSSArtifactStore`): native oss2
  client with optional STS token; artifact URIs stay backend-neutral
  (`artifact://jobs/<id>/...`) — only ArtifactRecord internals know the
  bucket. Use the `-internal` endpoint from inside ACK.
- **SLS** (`sls/aliyunlogconfig.yaml`): stdout JSON collection for the
  flashml namespace; every FlashML log line carries `job_id`, so SLS queries
  correlate 1:1 with Cloud jobs. Verify with:
  `* | where job_id = '<job-id>'` in the logstore query page.
- **Managed Prometheus**: enable the ARMS Prometheus add-on for the cluster;
  KubeRay/Ray expose metrics endpoints that its default service discovery
  scrapes. Set `FLASHML_PROMETHEUS_ENABLED=true` so the dashboard reports it.
  (No custom dashboards are part of the POC.)
- **Sandboxed Containers** (`sandbox/README.md`): separate secure node pool,
  fail-closed tier translation. Never claimed to work locally.

## Smoke test (run only with a real cluster)

1. `kubectl get nodes` shows the standard pool; FlashNode DaemonSet Ready.
2. `kubectl -n flashml get pods` — all services Running, images from ACR.
3. `make poc-ack-submit` → job SUCCEEDED.
4. Artifact objects exist in the OSS bucket (console or `ossutil ls`).
5. SLS query by `job_id` returns driver iteration logs.
6. Optional: sandbox demo pod runs on the secure pool.
