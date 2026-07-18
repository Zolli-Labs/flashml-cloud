# FlashML Dashboard

This is the retained Next.js dashboard. It is a real, buildable application,
but it is **not yet connected to the new `flashml` package**.

Current runtime path:

```text
apps/dashboard -> HTTP API -> legacy/coordinator/server.py
```

Phase 3 target:

```text
apps/dashboard -> HTTP API -> flashml serve -> flashml engine/providers
```

The frontend API contract should remain stable during that migration.

## Run it today

From the repository root, start the retained coordinator:

```bash
cd legacy/coordinator
../../.venv/bin/python server.py
```

In another terminal:

```bash
cd apps/dashboard
npm install
npm run dev
```

Open `http://localhost:3000`. The API defaults to `http://localhost:8000` and
can be overridden with `NEXT_PUBLIC_API_BASE`.

## Verify

```bash
cd apps/dashboard
npm run build
```

See [`docs/phases/03-serve-dashboard/README.md`](../../docs/phases/03-serve-dashboard/README.md)
for the migration requirements and missing work.
