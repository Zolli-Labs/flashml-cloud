# GitHub App private repos — implementation plan

Spec: `specs/2026-08-10-github-app-private-repos-design.md`.
Baseline before starting: API `991 passed, 1 skipped, 1 deselected, 1 xfailed`
— **green**, so the success criterion is "still green, plus the new tests".
Any failure that appears is mine.

TDD throughout: write the test, watch it fail for the right reason, then the
minimal code. Six tasks, each ending green and committable.

---

## Task 1 — `Settings` fields, inert when unset

**Red:** `test_settings.py` — `GITHUB_APP_ID`/`GITHUB_APP_SLUG`/
`GITHUB_APP_PRIVATE_KEY` land on `Settings`; `github_app_configured` is False
with none set, False with a partial set, True with all three; a base64 PEM
decodes and a raw PEM passes through unchanged.

**Green:** three fields plus a `github_app_configured` property and a
`_decode_pem` helper in `settings.py`. A half-configured App warns exactly as
half-configured mail does (`settings.py:179-184`) — the same failure shape
deserves the same signal.

**Not in this task:** any network call.

---

## Task 2 — `github_app.py`: JWT and token minting

**Red:** `test_github_app.py`
- `app_jwt` produces a token whose `iss` is the App id, whose `iat` is
  backdated, and whose `exp` is under 10 minutes — decoded with the **public**
  half of a keypair generated in the test, so a wrong signature fails.
- `installation_token` posts to `/app/installations/{id}/access_tokens`
  carrying that JWT and returns `(token, expires_at)`.
- A second call inside the validity window does **not** re-POST (cache hit);
  one past the 60s skew margin does.
- 404 → `GitHubAppError` flagged `uninstalled`; 401 → flagged `misconfigured`.

**Green:** `app_jwt`, `installation_token`, a module-level cache keyed by
installation id, and `GitHubAppError` carrying a `kind`.

**Watch for:** `jwt.encode` wants the PEM as bytes or a loaded key object.
Pass the loaded key — passing a `str` silently works for HS256 and raises for
RS256, which is a confusing error to meet at runtime.

---

## Task 3 — migration 0013 and the db accessors

**Red:** `test_db_github.py` against the ephemeral Postgres the other db tests
use
- insert then fetch by `(user_id, owner_login)`, case-insensitively — GitHub
  logins are case-preserving and case-insensitive, and a repo URL may be typed
  either way.
- **two users, one `installation_id`, both insert successfully** — the §5
  property. This test fails loudly against a single-column primary key, which
  is the point of writing it.
- delete removes one user's row and leaves the other's.
- state: insert, claim-once (second claim returns None), expired returns None,
  wrong user returns None.

**Green:** `0013_github_installations.sql` per spec §5, plus
`insert_github_installation`, `fetch_github_installation_for_owner`,
`list_github_installations`, `delete_github_installation`,
`insert_github_install_state`, `claim_github_install_state`.

---

## Task 4 — `repo.py`: the authenticated fetch

**Red:** extend `test_repo.py`
- no token → request goes to `codeload.github.com` (today's behaviour, pinned
  so this change cannot silently move it).
- token → request goes to `api.github.com/repos/{o}/{n}/tarball/{ref}` with
  the Bearer header.
- a 302 to codeload is followed and the body returned.
- **the `Authorization` header is absent on the redirected request** — spec
  §4. This is the assumption about httpx worth pinning.

**Green:** branch on `token` for the URL; `follow_redirects=True` is already
set (`repo.py:83`).

---

## Task 5 — routes and `from-repo` wiring

**Red:** `test_github_installations.py`
- `POST /install-url` returns a URL containing the slug and a state; the state
  row exists.
- `POST /installations` with a good state → 201 and a row.
- **with another user's state → 403 and no row** (spec §3).
- with an expired state → 400. Replayed → 400.
- `GET` lists only the caller's; `configured:false` when the App is unset.
- `DELETE` removes the caller's row; another user's id → 404.
- `from-repo` on a repo whose owner the user has an installation for mints a
  token and passes it to `fetch_repo`; with no installation, `fetch_repo` is
  called with `None`.

**Green:** the four routes; `fetch_repo`'s injected signature grows a
`token` parameter; `submit_job_from_repo` resolves the installation between
parsing and fetching.

**Watch for:** `fetch_repo`'s lambda at `app.py:802-804` is positional. Every
existing call site and test fake must move together or the from-repo tests
fail in a way that looks unrelated to this change.

---

## Task 6 — console, config, docs

- A GitHub section on the account settings page: Connect button (hidden
  entirely when `configured:false`), connected accounts with disconnect, and
  the `/settings/github/callback` page that posts `{installation_id, state}`.
- `render.yaml`: three vars on both API services, `sync: false`.
- `.env.example` / `.env.dev.example`: the same three, blank, with the
  base64-PEM note.
- `flashml-cloud/CLAUDE.md`: a short section — this is a second GitHub
  registration, not Supabase's provider.
- `ROADMAP.md` §6.2: amend to record the reversal and point at the spec.
- `PROGRESS.md`: one entry with real numbers.

---

## Definition of done

- API suite green, with the new tests; web `npm test`, `tsc --noEmit`, lint
  all clean.
- The wrong-user state test and the two-users-one-installation test both
  present and passing — those are the two properties that would be a real
  vulnerability and a real outage respectively.
- Unset App credentials: API boots, public submit works unchanged, console
  shows no Connect button. Proven by a test, not by inspection.
- No secret in any tracked file.

**Not done here, and stated so nobody assumes otherwise:** the App is not
registered on GitHub, so nothing has been exercised against real GitHub. The
first real install is an operator step (spec §9) and will be the first true
end-to-end evidence.
