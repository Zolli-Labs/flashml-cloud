-- 0024_linkedin_url.sql
--
-- The access request carries the applicant's LinkedIn profile. A human
-- screens every pilot request, and the profile is part of what they read;
-- it lives on the REQUEST row (like use_case) rather than profiles because
-- it is paperwork for the decision, not a fact the admitted user edits
-- forever.
--
-- Nullable on purpose: invite stubs (record_pending_invite) and rows that
-- predate this migration have no submission behind them, and a NULL
-- use_case already marks a stub, so NULL here is never ambiguous.

alter table public.access_requests add column if not exists linkedin_url text;

comment on column public.access_requests.linkedin_url is
    'Required on every real submission (parse_submission), NULL on invite '
    'stubs and pre-migration rows. Scheme-normalised server-side so what '
    'is stored is always clickable.';
