# Vocabulary sweep — retire "Zolli" and "Crew" from the interface

**Date:** 2026-08-10
**Status:** proposed design, awaiting owner review.
**Repos touched:** `flashml-cloud` only — console copy, five metadata titles,
two test files, one `CLAUDE.md` section. **No API change, no migration, no
route change, no component renames.**
**Roadmap item:** P2.3 subset (`ROADMAP.md`), owner decision §6.3 as amended.

**Origin.** Three words name one thing and two words name another. The
console's own `/how-it-works` page carries a callout apologizing for it
(quoted in §5). The product is being pointed at real users, and an invented
noun is an onboarding tax paid by every one of them.

---

## 1. Decisions

1. **Two words retire from the interface: "Zolli"/"Zollis" (which means a
   machine) and "Crew"/"Crews" (which means a workspace).** They become
   **machine** and **workspace**.
2. **"workspace" stays.** This reverses the first reading of owner decision
   §6.3 and is the amendment recorded there. The console's "workspace" was
   deliberate — `2026-08-03-workspace-console-design.md` §1 decision 5:
   *"'Pool' names a supply of compute; 'workspace' names a place people work
   together, and this design exists to make the console read as the second."*
   `CLAUDE.md` codifies it as *"Do not 'fix' one side to match the other."*
3. **The UI-says-workspace / API-says-pool split is kept on purpose,** so
   `CLAUDE.md`'s Vocabulary section stays true and only needs Zolli and Crew
   named as retired.
4. **No identifiers are renamed.** `WorkspaceProvider`, `useWorkspace`,
   `workspacePath`, `lib/workspace-scope.ts`, `WORKSPACE_TAB_ITEMS` and the
   `components/workspace/` directory all stay. This is the single biggest
   scope decision: the inventory found **364 `workspace` hits of which ~90%
   are identifiers and route literals, against ~25 lines of rendered copy.**
   Renaming symbols would be a large, risky diff for zero user-visible gain.
5. **No routes change.** `/w/[poolId]`, `/workspaces`, `/pools/join`,
   `/account/machines`, `/activate` are all untouched, so every bookmark,
   invite link and `?pool=` URL keeps working. `machines` already reads
   correctly in URLs.
6. **The brand survives intact.** "ZolliAI", "Zolli Labs", the `Zolli-Labs`
   GitHub org, `ghcr.io/zolli-labs` and the mascot system
   (`ZolliCharacter`, `ZOLLI_ROLES`, `lib/zolli-brand.ts`, the character
   artwork and its `aria-label`s) are **not** product nouns and are out of
   scope. Owner decision §6.3 defers the character question to marketing.
7. **One rule settles every remaining "crew" on the landing page:** the
   marketing voice may use *crew* as a **metaphor**, never as the **label of
   a product object**. So "Bring laptops, GPU rigs, and cloud instances
   together as one resilient compute crew" stays; the CTA **"Build your
   crew"**, which creates a workspace and links to `/workspaces`, becomes
   "Create a workspace". A visitor must never click a noun on the landing
   page and meet a different noun on the next screen. *Flagged for owner
   confirmation — this is the one judgment call in the spec.*

---

## 2. Scale — what actually changes

From a complete grep of `apps/web` and `apps/api`:

| Term | Total lines | Retiring (user-visible) | Staying |
|---|---|---|---|
| `Zolli` | 178 | **~76** copy where it means *machine* | ~34 brand · ~55 mascot/brand identifiers · 5 comments |
| `Crew` | 95 | **~57** console copy where it means *workspace* | ~25 landing metaphor (§1.7) · 8 landing component names · 1 comment |
| `workspace` | 364 | **0** — decision 2 | all of it |
| API `Zolli`/`Crew` | 25 | **0** — every hit is a comment, docstring, test fixture, or the `Zolli-Labs`/`ghcr.io` infra string | all |

**≈133 user-visible strings, plus 5 page-metadata titles.** No API surface,
no SQL, no routes.

Two findings worth keeping:

- **The API is clean.** No `HTTPException(detail=…)` anywhere contains any
  of these words, so no error toast can leak old vocabulary into the UI.
- **Seven of the 46 API `workspace` hits mean the *monorepo*, not the
  product noun** (`test_import_boundary.py:3`, `fedavg.py:18`,
  `0001_initial.sql:157`, …). They are correct as written. Decision 2 means
  nobody touches them anyway — noted so a future sweep does not.

---

## 3. Zolli → machine

The ~76 rendered strings, by screen (file:line from the inventory):

- **My Zollis** (`account/machines/page.tsx`) — `:97` heading, `:99`, `:119`,
  `:164` table header, `:208`/`:212`/`:213` toasts, `:320`/`:322` empty state.
- **Activate** (`activate/page.tsx`) — `:115`, `:147`, `:152`, `:155`, `:179`,
  `:190`, `:207` aria-label, `:209`, `:210`, `:285` button.
- **Console chrome** — `ConsoleShell.tsx:63` nav label, `:249`, `:264`, `:268`;
  `FleetPill.tsx:75`; `Shortcuts.tsx:15`, `:111`; `CommandPalette.tsx:43`,
  `:44`, `:139`, `:193`.
- **Workspace tabs** — `w/[poolId]/machines/page.tsx:24`, `:37`;
  `overview/page.tsx:36`; `submit/page.tsx:154`, `:214`, `:222`;
  `workspaces/page.tsx:72`.
- **Tables** — `PoolFleetTable.tsx:23`, `:35`; `MemberTable.tsx:19`;
  `YourMachines.tsx:59`, `:123`, `:125`, `:152`; `MemberCredits.tsx:41`;
  `RoundProgress.tsx:366`; `FleetTopology.tsx:291`; `EnrolInstructions.tsx:189`.
- **Jobs** — `jobs/[jobId]/page.tsx:471`, `:492`.
- **Docs** — `docs/page.tsx:26`, `:72`, `:74`, `:200`, `:216`, `:220`;
  `how-it-works/page.tsx:63`.
- **Not found** — `not-found.tsx:13`.
- **Landing** — `Hero.tsx:66`; `RecoveryDemo.tsx:33`, `:36`; `CrewStory.tsx:13`,
  `:23`, `:41`; `EventLedger.tsx:32`, `:33`, `:37`. *(Here Zolli means a
  machine even in marketing voice, so §1.7's metaphor exemption does not
  apply — these become "machine".)*

**Pluralisation.** `FleetTopology.tsx:291` hand-rolls
`Zolli{n === 1 ? "" : "s"}`. "machine"/"machines" is regular and `lib/plural.ts`
already exists — use it and delete the hand-rolled branch.

**Two lines that look like brand but are not:** `lib/zolli-brand.ts:31`
`subtitle: "New Zolli"` and `:44` `"Hands interrupted work to the next Zolli."`
are **rendered on the landing role cards** and mean *machine*. The file is
brand, these two strings are product copy.

---

## 4. Crew → workspace

The ~57 console strings:

- **Page metadata** (browser tabs, bookmarks, shared-link previews) —
  `pools/layout.tsx:3` "Crews", `pools/[poolId]/layout.tsx:3` "Crew",
  `pools/join/layout.tsx:3` "Join a Crew". Also `account/machines/layout.tsx:3`
  "My Zollis" and `activate/layout.tsx:3` "Activate a Zolli" from §3.
- **Join** — `pools/join/page.tsx:61`, `:145`, `:187`.
- **Create** — `workspaces/page.tsx:50`, `:60`, `:70`, `:72`, `:77`, `:98`,
  `:99`, `:112`, `:125`, `:131`.
- **Switcher / gate / provider** — `WorkspaceSwitcher.tsx:60`, `:69`, `:81`,
  `:123`; `WorkspaceGate.tsx:37`, `:45`; `WorkspaceProvider.tsx:111`.
- **Invites / rename** — `InviteManager.tsx:109`, `:142`, `:232`;
  `RenameWorkspace.tsx:52`, `:58`, `:86`.
- **Tabs** — `settings/page.tsx:47`, `:55` ("Crew ID" → "Workspace ID");
  `submit/page.tsx:206`, `:213`, `:214`, `:222`; `machines/page.tsx:22`, `:24`,
  `:40`; `jobs/page.tsx:71`, `:193`; `overview/page.tsx:124`.
- **Machines / onboarding** — `PoolFleetTable.tsx:23`; `YourMachines.tsx:110`,
  `:125`, `:126`, `:214` (a template-built `aria-label`);
  `ConnectPanel.tsx:176`, `:218`; `account/machines/page.tsx:99`;
  `PendingScreen.tsx:68`; `OnboardingForm.tsx:275`.
- **Jobs** — `jobs/[jobId]/page.tsx:202` back-button label
  (`"Jobs" : "Crews"`), `:463`.
- **Docs** — `docs/page.tsx:133`, `:144`, `:171`, `:172`, `:173`.

---

## 5. The apology callout, and the docs that explain the mix

`how-it-works/page.tsx:34-53` exists only to excuse the inconsistency:

> A note on names before the rest of this page: this console calls it a
> **workspace**. A few older screens — the switcher in the rail, some
> toasts, the submit form under a workspace — still say **Crew**, left over
> from before the rename and not yet swept. The API and the database call it
> `pool` throughout, so a raw error message will use that word. All three
> names point at the same row.

After the sweep two of its three claims are false. Replace it with the one
durable fact — the console says *workspace*, the API and database say
*pool*, and they are the same thing — or drop it and let the page describe
the model. Its surrounding comment ("Three words for one row in one table,
and not all of them agree yet") goes with it.

**`CLAUDE.md` / `AGENTS.md`** (the same file; one is a symlink) — its
Vocabulary section stays correct under decision 3 and gains one sentence:
*"Zolli" and "Crew" were retired from the interface on 2026-08-10; the
machine is a **machine** and the pool is a **workspace** in all user-facing
copy.* Without it a future agent reads only "workspace vs pool" and
reintroduces the old words.

---

## 6. Testing

1. **A guard test over `apps/web`**: no rendered string, `aria-label`,
   `placeholder`, or `metadata.title` contains `Zolli`/`Crew` as a standalone
   word, with an explicit allowlist for brand — `ZolliAI`, `Zolli Labs`,
   `Zolli-Labs`, `zolli-labs`, `components/brand/**`, `lib/zolli-brand.ts`
   (minus the two product lines in §3), and the landing metaphor strings
   permitted by §1.7. This test is the deliverable that keeps the sweep swept.
2. `lib/zolli-brand.test.ts` is a **brand** test and mostly stands; only the
   two product-copy assertions from §3 move.
3. API test housekeeping: `test_online_machine_count.py` uses `name="crew"`
   as pool fixture data (`:42`, `:51`, `:58`, `:67`, `:77`, `:92`) and
   `test_elastic_layout.py:60` /`test_db_access.py:348` carry the words in
   test *names*. Data and names only — no assertion breaks.
4. Existing web tests (`workspace-scope.test.ts`, `plural.test.ts`,
   `access-screen.test.ts`) must stay green untouched — proof that decision 4
   held and no identifier moved.

---

## 7. Known gaps to enumerate during implementation

The inventory was interrupted; two files were not fully read and **must** be
swept by grep during the work rather than trusted to this spec's line lists:

- `docs/page.tsx` — the **Glossary** section (`GLOSSARY`, `id: "glossary"`)
  was never read. It plausibly *defines* the retiring terms, which would make
  it the highest-value single edit in the sweep.
- `how-it-works/page.tsx` lines 121→end, including the `Ownership()` SVG's
  rendered text labels (comments at `:331`, `:345` imply labels reading
  "workspace" — which now stay, but the SVG may also render "Zolli"/"Crew").
- `e2e/*.py` was not grepped for these strings.

---

## 8. Out of scope

- Renaming any identifier, file, directory or route (decision 4, 5).
- The API's `pool` vocabulary, and the seven monorepo-sense `workspace`
  comments (§2).
- The mascot/character system and every `ZolliAI` / `Zolli Labs` brand string
  (decision 6).
- The `#crew` landing anchor and `CrewStory` / `CrewRoles` component names —
  landing-internal, covered by the metaphor rule, no product object named.
- Any copy change that is not a vocabulary change. Where a retiring string
  sits inside copy this sweep would otherwise improve, change the noun only;
  the first-run spec (`2026-08-10-first-run-quickstart-design.md`) rewrites
  several of the same strings for other reasons and must land the new
  vocabulary too.

---

## 9. Implementation plan

`plans/2026-08-10-vocabulary-sweep.md`. Mechanical, but do it in reviewable
slices rather than one find-and-replace — several strings are inside
template literals and `aria-label`s built at runtime
(`YourMachines.tsx:214`, `FleetTopology.tsx:291`).

1. The guard test from §6.1, written first and watched failing.
2. Zolli → machine (§3), including the `lib/plural.ts` fix.
3. Crew → workspace (§4).
4. The five metadata titles.
5. The callout and `CLAUDE.md` (§5).
6. The §7 gaps: read `docs/page.tsx` Glossary and `how-it-works` in full,
   sweep what the line lists missed, re-run the guard.

**Demo:** the guard test green, and a click-through of sign-in → workspaces →
overview → machines → activate → submit → job detail in which neither
retiring word appears.
