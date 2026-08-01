# flashml-cloud / apps/api

The FastAPI control-plane API. It is the **only** public door to the
coordinator (which runs as a Render private service with no public URL — see
`render.yaml`), and the only component that authenticates users and machines.

## Environment variables

| Variable | Required? | What it is |
| --- | --- | --- |
| `SUPABASE_URL` | **yes** (when auth is on) | Project URL, e.g. `https://<ref>.supabase.co`. The API derives the JWKS endpoint from it and verifies every browser token against the public key published there. |
| `SUPABASE_JWT_SECRET` | **no** — legacy | The old shared HS256 secret. Modern projects do not have one. See below. |
| `SUPABASE_SERVICE_KEY` | **yes** | Service-role key. Server-side only — it bypasses every ownership check, so it must never reach the browser or a `NEXT_PUBLIC_*` var. |
| `COORDINATOR_URL` | **yes** | Internal hostname of the coordinator. A bare `host:port` (what Render's `fromService: {property: hostport}` yields) is normalized to `http://host:port` at read time. |
| `COORDINATOR_OPERATOR_TOKEN` | **yes** | Unscoped operator credential for the coordinator. Anyone holding it can act as any machine — which is why the coordinator is private. |
| `DATABASE_URL` | for anything DB-backed | libpq connection string for Postgres. |
| `FLASHML_CONSOLE_URL` | no | Public base URL of the browser console, used to build the `verification_uri` a machine prints during device-code enrolment. Empty degrades to a relative path. |
| `FLASHML_REQUIRE_AUTH` | no (default on) | Set falsey only for local development. When on, a missing required setting above is a hard startup failure rather than a silently-open API. |

Missing a required value while auth is on raises at startup. That is
deliberate: an API that boots green and then 401s or, worse, runs open, is
far harder to notice than one that refuses to start.

## How Supabase tokens are verified — asymmetric ES256, no shared secret

**Modern Supabase projects sign access tokens with asymmetric keys and have
no shared secret to configure.** Our project's CURRENT key is `ECC (P-256)`,
so every newly-issued token carries `alg: ES256` and a `kid`. The API
verifies it against the matching public key from:

```
{SUPABASE_URL}/auth/v1/.well-known/jwks.json
```

`SUPABASE_JWT_SECRET` is therefore **optional**. `SUPABASE_URL` is the
mandatory input, because the keys are fetched from it. If you configure only
the secret and not the URL, the API will not start.

Two things worth knowing:

- **The JWKS is cached.** One `PyJWKClient` is built per project for the life
  of the process (300 s key lifespan). Fetching per request would put a
  network round trip in front of every authenticated call and let any
  anonymous caller hammer Supabase through us.
- **The JWKS being unreachable fails closed.** An unresolvable `kid`, a
  broken JWKS document, or a Supabase outage all raise `AuthError` → 401.
  Never an uncaught exception (an unauthenticated remote 500, and a
  500-vs-401 oracle), and never an accepted token.

### When you still want `SUPABASE_JWT_SECRET`

Set it if either applies:

- **Mid-rotation.** After switching to asymmetric keys, the old HS256 key
  stays listed as PREVIOUS and tokens already signed with it remain valid
  until they expire. With the secret set, those keep working; without it,
  every already-signed-in user is bounced early.
- **A self-hosted or older project** that still signs everything HS256.

The two paths never mix. The token header selects which path it is *judged
on*, and the algorithm list is then passed explicitly on both — an HS256
token is only ever checked against the configured secret, and a JWKS public
key is only ever used for the asymmetric algorithm the key itself declares.
That is what stops `alg=none` and the RS256→HS256 confusion attack, where an
attacker re-signs a token using the public key as an HMAC secret. An HS256
token arriving at a project with no secret configured is rejected outright.

See `flashml_cloud_api/auth.py` and `tests/test_auth.py`.

## Local setup

```bash
cd flashml-cloud/apps/api
python3 -m venv .venv
.venv/bin/pip install -e "../../../flashruntime[service]"   # same relative path the deploy uses
.venv/bin/pip install -e ".[dev]"
```

`pyjwt[crypto]` is a hard dependency: PyJWT cannot verify ES256 without
`cryptography`.

## Verify

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q -rs
```
