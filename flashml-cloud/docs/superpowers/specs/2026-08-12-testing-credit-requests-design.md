# Testing credits and approval requests

Approved product policy, 2026-08-12:

- one Zolli credit has a fixed cash value: **1 ZC = 1 USD**;
- a prospective starter grant is **10 ZC ($10)**;
- existing account balances and historical 250 ZC grants are not changed;
- a user may request a specific additional amount and explain the testing need;
- an admin may approve the requested amount, replace it with another positive
  amount, or decline the request;
- the same conversion is used both for display and for routing comparisons
  between ZC-priced community capacity and USD-priced rented capacity.

This is a change to the marketplace's currency doctrine, not display copy.
The existing implementation deliberately treats ZC and USD as incomparable.
Every comment, helper, API note, and test that asserts that separation must be
updated with the behavior so the old doctrine cannot be accidentally restored.

---

## 1. Credit policy

`marketplace.py` remains the authority for the credit unit. It will define:

```text
1 ZC = 1,000 millicredits = 1 USD
starter grant = 10,000 millicredits = 10 ZC = $10
```

The ledger continues to store integer millicredits. No floating-point value is
written to a balance or ledger entry. USD equivalents are derived at read time:
one millicredit is one tenth of a cent, so a 10,000 millicredit balance renders
as `10 ZC` and `$10.00`.

Changing the starter constant is prospective. Historical grant entries remain
append-only and existing balances are not reduced, rewritten, or reissued. The
current one-grant-per-spendable-account database invariant remains in force.
Migration `0021` marks every profile already present as starter-grant
ineligible, then changes the column default to eligible for profiles created
after the migration. Eligibility therefore follows account creation, not the
first time somebody happens to open the wallet.

Migration `0018_marketplace.sql` has already shipped and must not be edited.
Its comments that describe the old 250 ZC grant and non-convertibility are
superseded by comments applied in the new migration.

---

## 2. Credit request data model

A dedicated `public.credit_requests` table keeps testing-credit approval
separate from account-admission requests:

| column | contract |
|---|---|
| `id` | UUID primary key |
| `user_id` | requesting profile, cascading on account deletion |
| `requested_zc` | positive bigint millicredits entered by the user |
| `approved_zc` | positive bigint millicredits chosen by the admin; null until approved |
| `purpose` | trimmed, non-empty testing explanation, maximum 2,000 characters |
| `status` | `pending`, `approved`, or `declined` |
| `reviewed_by` | reviewing admin profile, nullable and `on delete set null` |
| `requested_at` | creation time |
| `reviewed_at` | decision time, null while pending |

A partial unique index on `user_id where status = 'pending'` permits historical
requests but only one active request per person. Check constraints enforce the
state shape: pending and declined rows have no approved amount; approved rows
have a positive approved amount and review timestamp.

RLS is enabled as defense in depth. The browser never talks to the table
directly; all reads and writes pass through the existing authenticated API.

There is currently a migration-number collision in the working tree:
`0019_price_quotes.sql` is tracked and `0019_ephemeral_machines.sql` is an
uncommitted lifecycle change. Before adding this schema, the lifecycle
migration becomes `0020_ephemeral_machines.sql`, and this feature uses
`0021_credit_requests.sql`.

---

## 3. Ledger approval contract

Approval mints the selected amount into the requester's spendable account as a
single `adjustment` entry:

```text
reason   = adjustment
ref_type = credit_request
ref_id   = credit_requests.id
delta_zc = approved_zc
```

The existing ledger idempotency index makes one adjustment per request and
spendable account possible. The decision function locks the pending request,
validates the admin-selected amount, ensures the requester's accounts exist,
posts the adjustment, and marks the request approved in one transaction.
Retrying an approval cannot mint a second grant. A second admin deciding the
same request receives the existing 404-style “nothing pending” response.

Declining records the reviewer and decision time but moves no credits. Neither
approval nor decline mutates the requested amount or purpose, preserving what
the user originally submitted.

---

## 4. API surface

User routes require an admitted account:

```text
GET  /v1alpha1/credit-requests
POST /v1alpha1/credit-requests
```

The GET route returns the user's request history newest first, including the
pending or most recent decision. POST accepts `{requested_zc, purpose}` where
`requested_zc` is integer millicredits, returns 201, and returns 409 if that
user already has a pending request.

Admin routes reuse the existing `admin_user` dependency:

```text
GET  /v1alpha1/admin/credit-requests?status=pending
POST /v1alpha1/admin/credit-requests/{request_id}/approve
POST /v1alpha1/admin/credit-requests/{request_id}/decline
```

Approval accepts `{approved_zc}` in integer millicredits. The admin list joins
profile context and current spendable/escrow balances so a reviewer can see the
request in context. Unknown statuses are 400; non-admins are 403; an absent or
already-decided pending request is 404.

No email integration is added in this slice. Users see status in the wallet,
and the existing admin console is the operational queue.

`GET /v1alpha1/credits` additionally returns a conversion object so the UI does
not hard-code product policy:

```json
{
  "usd_per_zc": 1,
  "spendable_usd": "10.00",
  "held_usd": "0.00"
}
```

USD display amounts are decimal strings. Ledger and request amounts remain
integer millicredits.

---

## 5. Wallet and admin experience

### Wallet

The `/market` wallet presents the spendable and escrow values in both units:

```text
10 ZC
$10.00 USD value
```

The page includes a **Request more credits** action. Its form contains:

- requested ZC amount, accepting up to three decimal places;
- required testing-purpose text;
- a submit action that previews the USD equivalent before submission.

When a request is pending, the form is replaced by its amount, purpose, date,
and `Pending review` state. Approved and declined decisions remain in a compact
request history. Approval shows both requested and approved amounts when the
admin changed the value.

### Admin queue

`/admin/requests` becomes a two-tab queue: **Access requests** and **Credit
requests**. The existing access workflow and cards remain unchanged.

Each credit-request card shows user identity, current spendable and held
balances, requested ZC and USD, purpose, and submission time. Approve opens an
amount field prefilled with the requested amount; the admin may change it before
confirmation. Decline requires no amount. Successful decisions optimistically
remove the card; failures restore it using the existing queue pattern.

---

## 6. Marketplace prices and routing

Marketplace listings retain their original settlement unit and add the fixed
equivalent:

```text
0.80 ZC/hour
$0.80/hour equivalent
```

Vendor capacity retains its USD price and may show the ZC equivalent where a
marketplace or wallet comparison calls for it. The API carries original and
normalized values separately; it never overwrites a RunPod USD quote with ZC
or a community listing with USD.

Routing normalizes every candidate to USD using `1 ZC = 1 USD` before price
ordering. For example, a community host at `0.80 ZC/hour` is more expensive
than RunPod at `$0.70/hour`; the cheapest plan may therefore select RunPod.
Venue preference may break an exact normalized-price tie, but it may no longer
place a more expensive venue ahead merely because the currencies differ.

Plan totals still report both original settlement totals and add a comparable
`total_usd_value`. Frontier dominance and cheapest-plan ordering use that
normalized total instead of treating ZC and USD as independent axes. The UI
continues to show the source totals so a user can see what will actually be
debited from the wallet and what will be charged by an external provider.

---

## 7. Validation and failure behavior

- requested and approved amounts must be positive, fit the ledger's signed
  bigint representation after conversion to millicredits, and contain at most
  three decimal places at the UI boundary;
- whitespace-only purposes are refused;
- a user cannot read or submit a request for another user;
- only admins can list or decide requests;
- one user cannot create two pending requests, including under concurrent
  submission;
- an edited approval credits exactly the approved amount, not the requested
  amount;
- approval retries and concurrent admin decisions cannot double-credit;
- declining does not move the ledger;
- historical 250 ZC accounts retain their balances;
- a new grant is 10 ZC and remains one-time;
- wallet and listing conversion uses integer/decimal arithmetic, never binary
  floating-point balance arithmetic;
- mixed ZC/USD candidate ordering and frontier dominance use normalized USD
  value while preserving original settlement totals.

API tests cover schema constraints, repository transactions, authentication,
authorization, route errors, and ledger idempotency. Web unit tests cover ZC
parsing, USD display, request states, admin amount edits, and mixed-currency
labels. Router tests prove the `$0.70` candidate beats the `0.80 ZC` candidate
and that equal normalized prices use the documented tie-break.

---

## 8. Explicitly out of scope

- purchasing ZC through Stripe or another payment processor;
- withdrawing ZC or converting it back to cash;
- automatic request approval;
- recurring monthly allowances;
- changing or clawing back existing balances;
- email notifications;
- multiple exchange rates or a floating ZC price.
