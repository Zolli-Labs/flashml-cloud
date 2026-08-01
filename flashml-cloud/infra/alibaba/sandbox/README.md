# ACK Sandboxed Containers — secure execution tier

Sandboxed Containers are a **separate secure cloud tier**, never available
locally and never on ACK Edge. The public JobSpec only expresses intent:

```yaml
isolation:
  tier: sandboxed        # or standard
  allowFallback: false
```

The ACK adapter translates that intent into deployment-owned wiring:

| JobSpec intent      | Deployment configuration (env on flashruntime)      |
|---------------------|-----------------------------------------------------|
| `tier: sandboxed`   | `FLASHML_SANDBOX_NODE_SELECTOR` (e.g. `flashml.dev/pool=secure-cloud`) |
|                     | `FLASHML_RUNTIME_CLASS` (RuntimeClass installed by ACK, e.g. `runv`) |
| `tier: standard`    | `FLASHML_STANDARD_NODE_SELECTOR`                    |

Fail-closed behavior (implemented and tested):

- Local profile / no sandbox pool configured → submission rejected 422:
  *"Sandboxed execution is unavailable in the local profile. Use a
  compatible ACK secure node pool."*
- `allowFallback: true` is the only way a sandboxed request may run on the
  standard runtime, and that choice is the job author's, never silent.

## Before enabling the pool, verify on the actual cluster

1. ACK cluster type/version supports Sandboxed Containers.
2. A dedicated node pool of a supported **ECS Bare Metal** instance family.
3. Node OS image supported by the sandbox runtime.
4. Network plug-in (Terway/Flannel) compatibility on that pool.
5. Storage: sandboxed pods have documented volume limitations — the POC
   workload only needs env vars + outbound OSS access, which is compatible.
6. `kubectl get runtimeclass` shows the sandbox RuntimeClass; put its exact
   name in `FLASHML_RUNTIME_CLASS`.
7. Label + taint the pool, e.g.
   `kubectl label node <n> flashml.dev/pool=secure-cloud` (or via node-pool
   config) and set `FLASHML_SANDBOX_NODE_SELECTOR=flashml.dev/pool=secure-cloud`.

## POC scope

The full Ray demo does **not** depend on Bare Metal capacity. If event
credits allow, run one small trusted pod on the sandbox pool as evidence
(`demo-pod.yaml` here), and label the pool nodes `flashml.dev/sandbox-capable=true`
so FlashNode reports the capability honestly.
