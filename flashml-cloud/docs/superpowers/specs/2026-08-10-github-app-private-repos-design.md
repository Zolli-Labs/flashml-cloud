# Private repositories via a GitHub App

**Date:** 2026-08-10. **Status:** design, approved to build.
**Roadmap item:** P1.2. **Migration:** `0013_github_installations.sql`.

## 0. The decision this reverses, on the record

`ROADMAP.md` §6.2 (owner, 2026-08-10) says *wait* on the GitHub App —
`from-upload` is the private-code path until a team asks for org-level
integration. The owner reversed that on 2026-08-10 and asked for the App
first. Recorded here so the roadmap and this spec do not silently disagree;
§6.2 should be amended to point at this document.

The practical consequence of building this one first: `from-upload` and the
`fmu_` CLI remain unbuilt, so the private-code story is **console-only** when
this ships. A developer with private code can submit it from a browser and
not from a terminal.

## 1. Decisions

1. **A GitHub App, not an OAuth app, and not a pasted PAT.** Per-repo consent,
   org-level install, no long-lived user credential in our database, and a
   token that expires in an hour on its own. `ROADMAP.md` P1.2's shape note
   already argued this; nothing found since disagrees.
2. **Supabase stays identity-only.** This is a second, separate GitHub
   registration. It is *not* Supabase's GitHub sign-in provider, and enabling
   that provider would not help — an OAuth `repo` scope is all-or-nothing,
   tied to one person's account, and tangles sign-in with authorization.
3. **The install is bound to the user by a single-use `state`,** minted by the
   API before the redirect. §3 explains why nothing weaker is safe.
4. **`(installation_id, user_id)` is the key, not `installation_id`.** See §5 —
   an installation is per-*account*, so two colleagues in one org legitimately
   share one id, and a single-column primary key locks the second one out.
5. **Fetching an authenticated tarball moves to `api.github.com`.** The
   existing seam points at the wrong host. See §4.
6. **Unconfigured is inert, not fatal.** With no App credentials the API boots
   and behaves exactly as it does today: public repos work, the console shows
   no Connect button. Same doctrine as mail (`settings.py:68-72`) and
   `console_url`.

## 2. What exists today

| Fact | Evidence |
|---|---|
| The token seam exists and is never given a token | `repo.py:58`, `app.py:802-804` |
| Its own docstring calls it a future private-repo seam | `repo.py:67-69` |
| It fetches from codeload with a Bearer header | `repo.py:74-77` |
| `from-repo` is the only pool-job entry point | `app.py:1867` |
| Preflight runs before any coordinator contact | `app.py:1913-1918` |
| RS256 signing is already available — no new dependency | `pyproject.toml` `pyjwt[crypto]`, cryptography 50.0.0 |
| Migrations are plain numbered SQL, RLS enabled, no policies | `migrations/0012_cli_credentials.sql` |

## 3. The threat that shapes the design

**An `installation_id` is not a secret.** It appears in GitHub URLs and in the
redirect back to us. If `POST /github/installations` accepted an id and bound
it to whoever called, an attacker who learned any id could bind another
organisation's installation to their own account and mint tokens that read
that org's private repositories. That is the whole feature, handed over by
guessing a small integer.

So the binding must prove *this user initiated this install*:

1. `POST /v1alpha1/github/install-url` (authenticated) mints a random
   `state`, stores `(state, user_id, expires_at)`, and returns
   `https://github.com/apps/<slug>/installations/new?state=<state>`.
2. GitHub redirects to the App's Setup URL with `installation_id`,
   `setup_action` and the same `state`.
3. `POST /v1alpha1/github/installations` accepts `{installation_id, state}`,
   requires the state row to exist, be unexpired, and belong to **the calling
   user**, then deletes it — single use.
4. Only then does the API call `GET /app/installations/{id}` with an App JWT
   to confirm the installation is real and read `account.login`.

Why this is sound: GitHub only permits an install on an account the person
administers. State proves the person who finished the flow is the person who
started it, in our session. Together: the binding user administers the
installed account.

Each of the weaker options fails concretely:

- **No state.** Bind-anyone, as above.
- **State not bound to a user.** Attacker mints a state, phishes a victim into
  installing with it; the victim's browser posts it, the API sees a valid
  state and binds the victim's org to whoever the attacker arranged.
- **State reusable.** One captured redirect binds repeatedly.

Failure mode when a victim is phished with an attacker's state: the state
belongs to the attacker, the victim is posting it, the user ids differ, and
the request is refused. That is the intended rejection, not an edge case.

## 4. Fetching an authenticated tarball

`repo.py:74` builds `https://codeload.github.com/{owner}/{name}/tar.gz/{ref}`
and, when a token is present, sends it as a Bearer header. **For a GitHub App
installation token against a private repo this is not the documented path and
is reported to 404.** The documented one is:

```
GET https://api.github.com/repos/{owner}/{repo}/tarball/{ref}
Authorization: Bearer <installation-token>
```

which answers `302` with a **pre-signed** codeload URL, valid five minutes for
a private repo. The signature is in the URL, so the redirect needs no
credential of its own.

Two consequences the implementation must respect:

- **Anonymous fetches keep using codeload, unchanged.** That path is working,
  is not rate-limited for public repos (`repo.py:64-65`), and every existing
  test covers it. Only the token-bearing branch moves hosts.
- **The `Authorization` header must not survive the cross-host redirect.**
  httpx strips it when the host changes, which is what we want — the codeload
  URL is pre-signed and GitHub rejects a request carrying both. This is
  asserted by a test rather than assumed, because it is a library behaviour we
  depend on and would not otherwise notice changing.

`extract_safely` is untouched. The bytes were already fully untrusted
(`repo.py:1-8`); a private repo's bytes are no more trusted than a public
one's.

## 5. Schema — `0013_github_installations.sql`

```sql
create table if not exists public.github_installations (
    installation_id  bigint not null,
    user_id          uuid   not null references public.profiles(id) on delete cascade,
    account_login    text   not null,
    account_type     text   not null,
    repository_selection text not null default 'selected',
    created_at       timestamptz not null default now(),
    primary key (installation_id, user_id)
);

create table if not exists public.github_install_states (
    state       text primary key,
    user_id     uuid not null references public.profiles(id) on delete cascade,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);
```

**Why the composite key.** An installation belongs to a GitHub *account*, not
to a person. When `acme` installs the App once, every colleague who connects
is handed the same `installation_id`. With `installation_id` as a single-column
primary key the first colleague to connect wins and every subsequent one
collides on insert — the feature would appear to work for whoever went first
and be permanently broken for their team, which is the exact opposite of what
an org-level integration is for.

**Why a state table rather than a signed cookie.** The repo already models
short-lived redemption codes as rows (`device_codes`, `migrations/0012`), the
API has no signing secret of its own, and expiry is a `delete` the same
sweeper style already handles. One less secret to configure.

`account_login` is stored so submit-time resolution is a single indexed
lookup against the repo owner already parsed out of the URL
(`app.py:1924`), with no GitHub call on the hot path.

## 6. Minting a token

`github_app.py`, no state beyond a cache:

- `app_jwt(settings)` — RS256, `iss` = App ID, `iat` backdated 60s for clock
  skew, `exp` +9 min (GitHub's ceiling is 10).
- `installation_token(settings, installation_id)` —
  `POST /app/installations/{id}/access_tokens` with that JWT, returns
  `(token, expires_at)`. GitHub issues these for one hour.
- Cached in-process by `installation_id` until 60s before expiry. A submit
  is a handful of seconds; re-minting per submit would be a needless round
  trip on every job, and the cache is memory-only so a restart simply mints
  again.

Errors are typed, because they mean different things to a user:

| GitHub says | Means | User sees |
|---|---|---|
| 404 on the installation | uninstalled since connecting | 400 "the GitHub App is no longer installed on `<login>` — reconnect" |
| 404 on the tarball, install is fine | repo not in the selected set | 400 "`<owner>/<name>` is not one of the repositories this installation can read" |
| 401 minting | our App key is wrong | 502, logged, never the user's fault |

## 7. Routes

All `tags=["browser"]`, all behind `admitted_user` — this is job-author
surface and follows `from-repo`'s gate exactly.

- `POST /v1alpha1/github/install-url` → `{url}`; mints state.
- `GET  /v1alpha1/github/installations` → `{installations: [...], configured: bool}`.
  `configured:false` when the App is unset, which is what the console reads to
  decide whether to render a Connect button at all.
- `POST /v1alpha1/github/installations` `{installation_id, state}` → 201.
- `DELETE /v1alpha1/github/installations/{installation_id}` → 204. Removes
  **our** row only; it deliberately does not uninstall the App from GitHub,
  which is the org admin's decision and not ours to make from a job console.

`submit_job_from_repo` gains one step, between parsing the repo and fetching
it: look up an installation for `(user_id, owner)`; if found, mint a token and
pass it to `fetch_repo`. No installation means no token, which means today's
exact behaviour — a public repo works, a private one 404s with the message it
already gives.

## 8. Testing

Every GitHub hop is injected, matching how `fetch_repo` and the coordinator
transport are already injected (`app.py:780-804`), so no test reaches the
network:

- **`repo.py`**: an `httpx.MockTransport` asserting the token branch requests
  `api.github.com`, the anonymous branch requests `codeload`, the redirect is
  followed, and the `Authorization` header is **absent** on the second hop.
- **`github_app.py`**: JWT claims verified by decoding with the public half of
  a test keypair generated in-process; cache hit/miss around expiry; each
  error class mapped.
- **State**: expired refused, wrong-user refused, replay refused. The
  wrong-user case is the security property from §3 and gets a test whose name
  says so.
- **Composite key**: two users, one `installation_id`, both succeed — the §5
  failure written as a test.
- **`from-repo`**: private fetch gets a token; no installation gets `None`;
  a token is never minted for a repo owner the user has no installation for.

## 9. Operator setup — only the owner can do this

Code cannot register a GitHub App. Before any of this works end to end:

1. Create the App (Settings → Developer settings → GitHub Apps → New).
2. Permissions: **Repository → Contents: Read-only** and **Metadata:
   Read-only**. Nothing else. No webhook needed for this slice.
3. Setup URL: `<console>/settings/github/callback`, "Redirect on update" on.
4. Generate a private key; set `GITHUB_APP_ID`, `GITHUB_APP_SLUG` and
   `GITHUB_APP_PRIVATE_KEY` on both API services.
5. The PEM is multi-line. Render env vars take newlines, but `.env` files do
   not — store it base64-encoded and decode on read, so one format works
   everywhere.

## 10. Non-goals

- **No webhooks.** An uninstall is discovered when a mint 404s, and handled
  there. A webhook endpoint is public attack surface for a state we can
  observe lazily.
- **No write access, ever.** Contents:read and Metadata:read. FlashML reads
  code to run it and has no reason to push.
- **No org-wide sharing.** A connection is per-user. Two colleagues each
  connect; neither inherits the other's. Sharing an installation across a
  pool's members is a real feature and a different one, with its own consent
  question.
- **No private repos for `from-upload` or the CLI.** Both are unbuilt.
