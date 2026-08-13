"""What the trade-off route may and may not SAY about a price.

Separate from ``test_job_tradeoff.py`` — which pins the *arithmetic* — because
these pin the *sentence*, and the sentence is what a reader believes.

The defect this file exists to prevent already shipped once. ``price_reason``
ended with **"Another SKU costs more"**, which tells a reader the quoted rate
is the floor. It was false: the claim ranges only over SKUs that publish a
rate here, and the venue this deployment actually brings capacity up at —
``ecs-gpu`` — publishes none and cost roughly 8x the quoted figure when it was
checked.

It was also visibly self-contradicting. The ``RoutingCard`` on the same tab
renders ``VenueFit.reason`` verbatim, and several ``ecs-gpu`` fit reasons in
``router/venues.py`` carry the higher rate as a literal string. One screen,
two prices, with the lower asserted to be the minimum.

**Every number in that sentence was real and sourced. The arithmetic was never
wrong — the claim around it was.** That is the class of defect these tests
watch for, and it is invisible to any test that only checks figures.
"""
from __future__ import annotations

from flashml_cloud_api.app import _TRADEOFF_MAX_RENTED_STEPS

from test_job_tradeoff import (  # noqa: F401 - fixtures
    client,
    db,
    make_job,
    public_sandboxed_spec,
    seed_evidence,
    settings,
    sweep_spec,
    tradeoff,
    workspace,
)

#: Spellings of "nothing is cheaper". Phrased as a ban on the CLAIM rather
#: than on the one string that shipped, because a completeness claim has many
#: spellings and only one of them was caught by review.
COMPLETENESS_CLAIMS = (
    "another sku costs more",
    "nothing costs less",
    "no venue is cheaper",
    "the cheapest available",
    "cheapest anywhere",
    "lowest price available",
)


def _priced_body(db, client) -> dict:
    """A job where renting is both suited and priced, so `price_reason` exists."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)
    seed_evidence(db, job)
    return tradeoff(client, owner, job)


def test_the_price_sentence_makes_no_completeness_claim(db, client):
    reason = (_priced_body(db, client)["renting"].get("price_reason") or "").lower()
    for forbidden in COMPLETENESS_CLAIMS:
        assert forbidden not in reason, (
            f"price_reason claims completeness ({forbidden!r}). It may only "
            f"range over rates this API has actually observed — and the venue "
            f"this deployment rents from publishes none of them."
        )


def test_a_price_comparison_names_whose_skus_it_compares(db, client):
    """"every other published <provider> SKU costs more" is true and useful.
    "another SKU costs more" is neither. The difference is the scope, so the
    scope has to be present."""
    renting = _priced_body(db, client)["renting"]
    reason = renting.get("price_reason")
    if not reason:
        return
    provider = (renting.get("price") or {}).get("provider")
    if provider:
        assert provider in reason, (
            "a sentence comparing SKUs must name whose SKUs, or the "
            "comparison silently ranges over every venue including the ones "
            "that published no rate."
        )


def test_the_quote_is_labelled_cheapest_listed_not_cheapest_viable(db, client):
    """``_tradeoff_rented_price`` selects on ``has_gpu`` alone.

    ``gpus_per_task`` never narrows the row, so a job needing 80 GB or two
    GPUs per task is still quoted a 24 GB single-card machine. Quoting a job a
    machine that could not finish it is a money error in the user's disfavour;
    it is stated rather than left to be discovered.
    """
    reason = (_priced_body(db, client)["renting"].get("price_reason") or "").lower()
    if not reason:
        return
    assert "listed" in reason, (
        "price_reason must mark the quote as the cheapest LISTED machine-hour "
        "rather than the cheapest one that could actually run this job."
    )


def test_a_manually_acquired_venue_says_a_person_starts_that_machine(db, client):
    """``acquisition: manual`` is the difference between a rate we can act on
    and a rate somebody else's pod happens to publish. A reader shown the
    number without it reasonably assumes we can buy at it."""
    renting = _priced_body(db, client)["renting"]
    reason = renting.get("price_reason") or ""
    provider = ((renting.get("price") or {}).get("provider") or "").lower()
    if reason and provider == "runpod":
        assert "person" in reason.lower(), (
            "RunPod is acquisition: manual — the sentence must say a person "
            "starts that machine, or the rate reads as one we can acquire at."
        )


def test_no_price_sentence_when_renting_cannot_run_this_work(db, client):
    """A job a rented machine can never run gets no pricing commentary at all.

    How well we priced a machine this job cannot use is noise, and it reads as
    a decision we made — "we looked at renting and it was expensive" rather
    than "renting is not available to this job". ``reason`` already carries
    the whole answer.
    """
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=public_sandboxed_spec(4))
    seed_evidence(db, job)
    renting = tradeoff(client, owner, job)["renting"]

    assert renting["suited"] is False, "fixture no longer models a public job"
    assert renting.get("price_reason") is None, (
        "price_reason must be withheld when renting.suited is false."
    )
    assert renting.get("reason"), "the refusal itself must still be stated"


def test_the_sweep_cap_is_large_enough_that_its_caveat_stays_rare():
    """A caveat is only honest if it is unusual.

    At 16 the truncation note fired on essentially every real HPO sweep, so
    the caveat became the normal answer and the panel stopped answering its
    own question. This pins the intent rather than the number: lower the cap
    back under a realistic sweep and it fails.
    """
    assert _TRADEOFF_MAX_RENTED_STEPS >= 48, (
        "the rented-fleet sweep cap is low enough that its truncation note "
        "fires on ordinary sweeps, which makes the caveat the answer."
    )
