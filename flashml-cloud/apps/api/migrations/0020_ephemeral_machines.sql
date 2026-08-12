-- 0019_ephemeral_machines.sql
-- Rental sessions are not durable hosts. A RunPod or Colab filesystem can be
-- handed to another account, so keeping its FlashNode identity active forever
-- both leaves a dead row in the previous owner's fleet and makes a reused
-- state directory collide with that owner.

alter table public.machines
    add column lifecycle text not null default 'persistent'
    check (lifecycle in ('persistent', 'ephemeral'));

alter table public.device_codes
    add column lifecycle text not null default 'persistent'
    check (lifecycle in ('persistent', 'ephemeral'));

comment on column public.machines.lifecycle is
    'persistent hosts keep their identity until owner revocation; ephemeral '
    'rental sessions are revoked after their heartbeat grace period.';

comment on column public.device_codes.lifecycle is
    'Lifecycle requested by the enrolling agent and copied to machines at approval.';
