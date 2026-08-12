# Handoff prompt — paste this into a fresh frontend session

---

You are picking up the FlashML console UI. Work in `~/Work/Zolli-Labs/flashml-cloud`.

**Read these three, in this order, before touching anything:**

1. `flashml-cloud/docs/superpowers/specs/2026-08-12-console-ui-plan.md` — your spec and priority order.
2. `flashml-cloud/docs/superpowers/specs/2026-08-12-shipped-and-verified.md` — what landed on 2026-08-12, what is proven live, and §7 what is NOT.
3. `flashml-cloud/CLAUDE.md` and `flashml-cloud/flashml-cloud/CLAUDE.md` — house rules. Note the vocabulary rule: the UI says **workspace**, the API says **pool**. They are the same thing. Do not "fix" either side.

## Your first task is to look at it

`https://flashml-dev-web.onrender.com` — sign in, open a job, and *look*. Four
jobs already succeeded on dev with real artifacts, checkpoints and OSS mirrors,
so every page has real data rather than fixtures.

Do this before writing code. On 2026-08-12, three separate features passed
their tests and did not work: artifact downloads 401'd, a dataset glob found
nothing, and OSS mirroring was silently unconfigured. **"Tests pass" has not
meant "works" on this project.** Write down what you actually see — that list
is your real backlog, and it outranks the spec where they disagree.

## The one thing that will derail you

**The marketplace has ZERO HTTP routes.** `marketplace.py` (ledger, listings,
bids, matching) and `prices.py` (quotes with provenance) have 105 tests between
them, and `app.py` imports them only for a credits conversion and a price
label. **You cannot build the marketplace UI as a frontend-only task** — the
API surface has to come first. §3.1 of the plan lists the seven routes and the
three domain invariants that surface will not forgive. Read `marketplace.py`'s
module docstring before designing any of it.

## Priority

§A marketplace (API routes first, then UI) → §B finish the surfaces that exist
and align the console's look with the landing page → §C is post-deadline, skip
it. **Submission deadline is 2026-08-15.**

On styling: `app/globals.css` holds ~295 design tokens (Tailwind v4 `@theme`;
there is no `tailwind.config.*`). Those tokens are the single source of truth.
`app/(marketing)` and `components/landing/` are the visual reference to match.
Never introduce a colour that is not a token — if it is missing, add the token.
Audit and write findings before changing anything.

## Non-negotiable conventions

- **Decision layer in `lib/` (tested, vitest), markup in `components/`.**
  `lib/job-routing.ts`, `lib/job-artifacts.ts`, `lib/task-checkpoints.ts` are
  the pattern — read one before writing a new one.
- **Never render a number, name, size or timestamp the API did not return.** No
  placeholders, no sample data, no optimistic guesses.
- **A failed read must never render as an empty result.** Every panel needs
  loading / present / empty / unreadable as four distinct states.
- `null` means *not observed*, never `0`.
- **ZC and USD are shown side by side and NEVER summed.** There is no exchange
  rate; no field may imply one.
- **No fixture-shaped or credential-shaped literals anywhere.** The owner has
  rejected these explicitly. Build test fixtures at runtime.

## Verification gate — run all of these, every time

```
cd flashml-cloud/apps/web
npm test && npx tsc --noEmit && npm run lint && npm run build
```

`npm run build` needs `flashml-cloud/.env.dev` sourced (`set -a; . ./.env.dev; set +a`)
— `next.config.ts` hard-fails without `NEXT_PUBLIC_CLOUD_API`.

If you touch `app.py`: `cd flashml-cloud/apps/api && .venv/bin/python -m pytest -q`

**Baselines to beat, never regress:** web **767 passed / 46 files** · api
**2177 passed, 2 skipped, 3 deselected, 1 xfailed**.

## Environment

- Dev API `https://flashml-dev-api.onrender.com` · console `https://flashml-dev-web.onrender.com`
- Demo workloads: `Zolli-Labs/flashml-demo-suite`, branches `train` / `hpo` /
  `federated` / `evaluate`. Submit with
  `POST /v1alpha1/jobs/from-repo  {"repo": "...", "ref": "<branch>"}`.
- **Pushing to `develop` auto-deploys all three dev services.** Commit freely;
  push when a change is verified, and expect it to go live within ~2 minutes.

Ask the owner before: creating public repos or buckets, changing Alibaba or
Render configuration, or anything that spends money.
