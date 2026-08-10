-- 0013_github_installations.sql
--
-- Reading a submitter's PRIVATE repository, via a GitHub App installation.
--
-- WHAT IS STORED, AND WHAT DELIBERATELY IS NOT. Only an installation id, and
-- who connected it. No token, no refresh token, no OAuth grant — an
-- installation id is useless to anyone without the App's private key, which
-- lives in the environment and never in this database. That asymmetry is the
-- entire argument for a GitHub App over an OAuth `repo` scope: a leak of this
-- table grants nobody anything, and the person who installed can revoke it
-- from GitHub without asking us.
--
-- THE PRIMARY KEY IS COMPOSITE, AND THAT IS LOAD-BEARING. GitHub installs an
-- App on an ACCOUNT, not on a person: when `acme` installs once, every
-- colleague who connects is handed the SAME installation_id. With
-- installation_id alone as the primary key the first colleague to connect
-- wins and every subsequent one collides on insert — the feature would appear
-- to work for whoever went first and be permanently broken for their team,
-- which is the precise opposite of what an org-level integration is for.
-- Pinned by `test_two_users_can_share_one_installation_id`.

create table if not exists public.github_installations (
    installation_id       bigint not null,
    user_id               uuid not null
                          references public.profiles(id) on delete cascade,
    account_login         text not null,
    account_type          text not null,
    repository_selection  text not null default 'selected'
                          check (repository_selection in ('all', 'selected')),
    created_at            timestamptz not null default now(),
    primary key (installation_id, user_id)
);

comment on table public.github_installations is
    'A GitHub App installation a user has connected to their account. Holds '
    'no credential: the installation id is exchanged for a one-hour token at '
    'submit time, signed with the App private key from the environment. The '
    'primary key is composite because one organisation installation is '
    'shared by every colleague who connects it.';

comment on column public.github_installations.account_login is
    'The org or user the App is installed on. Stored so submit-time '
    'resolution is one indexed lookup against the repo owner already parsed '
    'out of the URL, with no GitHub call on the hot path. Compared '
    'case-insensitively — GitHub logins preserve case but compare without it.';

comment on column public.github_installations.repository_selection is
    '''all'' or ''selected''. Advisory only: it explains a 404 on a repo the '
    'installation cannot see, and is never used to authorize anything — '
    'GitHub is the authority on what a token may read.';

alter table public.github_installations enable row level security;

create index if not exists github_installations_owner_idx
    on public.github_installations (user_id, lower(account_login));

-- The one-use, user-bound token that ties "someone finished an install on
-- GitHub" to "that same someone started it here".
--
-- WHY THIS TABLE EXISTS AT ALL. An installation_id is not a secret — it
-- appears in GitHub's own URLs and in the redirect back to us. Without this
-- binding, anyone who learned an id could POST it and attach another
-- organisation's installation to their own account, then mint tokens that
-- read that organisation's private repositories. The whole feature, handed
-- over by guessing a small integer.
--
-- A ROW RATHER THAN A SIGNED COOKIE. `device_codes` (0012) already models
-- short-lived redemption this way, the API holds no signing secret of its
-- own, and expiry is a delete. One less secret to configure and rotate.

create table if not exists public.github_install_states (
    state       text primary key,
    user_id     uuid not null references public.profiles(id) on delete cascade,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);

comment on table public.github_install_states is
    'Single-use CSRF state for the GitHub App install redirect, bound to the '
    'user who started the flow. Claimed exactly once and only by that user; '
    'a claim by anyone else fails and leaves the row for its rightful owner.';

alter table public.github_install_states enable row level security;

create index if not exists github_install_states_expiry_idx
    on public.github_install_states (expires_at);
