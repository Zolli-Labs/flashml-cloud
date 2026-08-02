# FlashML System Overview — moved

This was a **copy**. The document itself lives in the public monorepo:

**https://github.com/Zolli-Labs/flashml/blob/main/flashruntime/docs/SYSTEM_OVERVIEW.md**

If you have the sibling checkout, it is at
`../flashml/flashruntime/docs/SYSTEM_OVERVIEW.md`.

## Why this is a pointer and not the document

Until 2026-08-01 the same 284-line file existed three times — once in
`flashruntime`, once in `flashnode`, once here — kept in agreement by a
hand-run `make sync-docs` and policed by a `make check-docs` that diffed the
copies. That is the same shape of problem as the git-subtree mirroring this
migration removed: one source of truth, several copies, and a human in the
loop remembering to reconcile them.

`flashruntime` and `flashnode` now share one repository, so two of the three
copies collapsed on their own. This one is replaced by a link. `sync-docs` and
`check-docs` are gone from the Makefile; there is nothing left to sync.

Edit the canonical file in the public repo. Nothing needs copying afterwards.
