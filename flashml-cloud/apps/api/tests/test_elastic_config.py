"""``version: 2`` federated configs: ``epochs`` + ``sync_every``.

The fields this file adds and the fields it refuses are two halves of one
change. ``shards`` and ``min_participants`` asked the submitter how many
pieces to cut the work into and how many machines to wait for — two numbers
nobody can know before a job starts, because the fleet changes while it
runs. The runtime now derives both from data coverage
(``flashml_workloads.chunks``), so the only inputs left are the two training
decisions: how much training (``epochs``) and how often to combine
(``sync_every``).

Every refusal below asserts on the *message*, not just the raise. A config
that used to work and now does not is the one moment a person needs to be
told what replaced it; "unknown key 'shards'" would send them looking for a
typo.

Spec: ``docs/superpowers/specs/2026-08-09-elastic-work-distribution-design.md``
§6.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api.flashml_yaml import (
    ConfigError,
    MAX_ROUNDS,
    derived_round_count,
    parse_flashml_yaml,
)

FEDERATED_V2 = """
version: 2
name: acme-fed
image: python-slim
entrypoint: train.py
mode: federated
epochs: 5
"""

INDEPENDENT_V1 = """
version: 1
name: acme-sweep
image: python-slim
entrypoint: train.py
"""


def _federated(**fields: object) -> str:
    lines = "".join(f"{k}: {v}\n" for k, v in fields.items())
    return FEDERATED_V2 + lines


# ---------------------------------------------------------------------------
# what a v2 federated config now says
# ---------------------------------------------------------------------------


def test_epochs_is_the_training_input():
    config = parse_flashml_yaml(FEDERATED_V2)
    assert config.epochs == 5


def test_sync_every_defaults_to_one_combine_per_pass():
    """The default has to reproduce today's behaviour exactly: one combine
    per pass over the data, so ``rounds == epochs``."""
    config = parse_flashml_yaml(FEDERATED_V2)
    assert config.sync_every == 1.0
    assert config.round_count == 5


def test_combining_more_often_than_once_a_pass_is_refused_with_the_reason():
    """The schema accepts ``sync_every`` and the range is validated, but only
    ``1.0`` can be honoured until a round worker walks a chunk sequence. The
    message has to say that, and say what to set instead — a bare "invalid
    value" would read as a typo in a field our own docs describe.
    """
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(_federated(sync_every=0.5))
    message = str(excinfo.value)
    assert "chunks_done" in message
    assert "epochs" in message


def test_the_round_count_still_follows_sync_every():
    """The derivation is live even while only one value reaches it — it is
    what the console shows and what the driver's resume check compares
    against."""
    assert derived_round_count(5, 1.0) == 5
    assert derived_round_count(5, 0.5) == 10
    assert derived_round_count(5, 0.1) == 50


def test_an_independent_config_has_no_federated_numbers():
    """``None`` rather than a plausible default: a caller that reads these
    without checking the mode should get a TypeError, not a silent 1."""
    config = parse_flashml_yaml(INDEPENDENT_V1)
    assert (config.epochs, config.sync_every, config.round_count) == (None, None, None)


# ---------------------------------------------------------------------------
# the fields that left, and the message that names their replacement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value, names",
    [
        ("rounds", 3, "epochs"),
        ("min_participants", 2, "epochs"),
        ("shards", 2, "epochs"),
    ],
)
def test_a_removed_field_names_what_replaced_it(field, value, names):
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(_federated(**{field: value}))
    message = str(excinfo.value)
    assert field in message
    assert names in message


def test_min_participants_says_why_a_machine_count_is_gone():
    """Not just "removed": the reason is that machines now contribute
    unequally, so counting them stopped meaning anything."""
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(_federated(min_participants=2))
    assert "coverage" in str(excinfo.value)


def test_version_1_federated_is_refused_with_the_migration():
    text = INDEPENDENT_V1 + "mode: federated\nepochs: 5\n"
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(text)
    message = str(excinfo.value)
    assert "version: 2" in message
    assert "federated" in message


def test_version_1_independent_still_parses():
    """A sweep config that worked yesterday is untouched by this change."""
    assert parse_flashml_yaml(INDEPENDENT_V1).version == 1


def test_version_3_is_still_refused():
    with pytest.raises(ConfigError, match="version"):
        parse_flashml_yaml(INDEPENDENT_V1.replace("version: 1", "version: 3"))


# ---------------------------------------------------------------------------
# refusals on the new fields
# ---------------------------------------------------------------------------


def test_federated_requires_epochs():
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(FEDERATED_V2.replace("epochs: 5\n", ""))
    assert "epochs" in str(excinfo.value)


@pytest.mark.parametrize("value", [0, -1, "five", True])
def test_epochs_must_be_a_positive_integer(value):
    with pytest.raises(ConfigError, match="epochs"):
        parse_flashml_yaml(FEDERATED_V2.replace("epochs: 5", f"epochs: {value}"))


@pytest.mark.parametrize("value", [0, 1.5, -0.5, "half"])
def test_sync_every_outside_one_pass_is_refused(value):
    """The runtime's own bound: a round's coverage target is ``sync_every``
    of one pass, and coverage cannot exceed the pass it measures."""
    with pytest.raises(ConfigError, match="sync_every"):
        parse_flashml_yaml(_federated(sync_every=value))


def test_sync_every_may_be_an_integer_one():
    """``sync_every: 1`` is the same request as ``1.0`` and YAML gives no
    way for the author to know which the parser wants."""
    assert parse_flashml_yaml(_federated(sync_every=1)).sync_every == 1.0


def test_epochs_above_the_round_cap_is_refused():
    """Each round is a full submit/lease/commit cycle across volunteer
    machines, so a four-digit count is a runaway and not a plan. At today's
    only ``sync_every`` the cap is reached through ``epochs`` alone; it is
    re-checked against the DERIVED count so it still holds the day
    ``sync_every`` starts multiplying it."""
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(_federated(epochs=MAX_ROUNDS + 1))
    assert str(MAX_ROUNDS) in str(excinfo.value)


@pytest.mark.parametrize("field, value", [("epochs", 5), ("sync_every", 0.5)])
def test_a_training_field_on_an_independent_job_is_refused(field, value):
    """Same rule the removed federated keys had: a config that names
    ``epochs`` has an author who believes several passes are happening."""
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(INDEPENDENT_V1 + f"{field}: {value}\n")
    assert field in str(excinfo.value)


# ---------------------------------------------------------------------------
# the entrypoint's side of the contract
# ---------------------------------------------------------------------------


def test_preflight_refuses_an_entrypoint_that_reports_no_chunks(tmp_path):
    """The failure this catches is the quietest one in the system.

    ``run_fedavg`` credits a machine only for chunk ids it reported and that
    the coordinator can prove it handed out. An entrypoint that writes
    ``samples`` and ``loss`` but no ``chunks_done`` therefore contributes
    nothing to the average — every round reduces zero contributions after
    every volunteer has trained, uploaded and spent real electricity. Nothing
    downstream reports it as an error, because from the driver's side a
    machine that reports no chunks is indistinguishable from one that did no
    work.
    """
    from flashml_cloud_api.images import resolve_image
    from flashml_cloud_api.preflight import preflight

    entry = """
import json, pathlib
weights = pathlib.Path("/work/inputs/weights.json")
delta = {"w": {"shape": [1], "data": [0.5]}}
pathlib.Path("/work/out/delta.json").write_text(json.dumps(delta))
pathlib.Path("/work/out/metrics.json").write_text(
    json.dumps({"samples": 100, "loss": 0.25})
)
"""
    (tmp_path / "train.py").write_text(entry)
    config = parse_flashml_yaml(FEDERATED_V2)
    findings = preflight(config, tmp_path, resolve_image(config.image))
    contract = [f for f in findings if f.code == "federated-contract"]
    assert contract, [f.code for f in findings]
    assert "chunks_done" in contract[0].message


def test_preflight_accepts_an_entrypoint_that_reports_its_chunks(tmp_path):
    from flashml_cloud_api.images import resolve_image
    from flashml_cloud_api.preflight import preflight

    entry = """
import json, pathlib
weights = pathlib.Path("/work/inputs/weights.json")
delta = {"w": {"shape": [1], "data": [0.5]}}
pathlib.Path("/work/out/delta.json").write_text(json.dumps(delta))
pathlib.Path("/work/out/metrics.json").write_text(
    json.dumps({"samples": 100, "loss": 0.25, "chunks_done": [0]})
)
"""
    (tmp_path / "train.py").write_text(entry)
    config = parse_flashml_yaml(FEDERATED_V2)
    findings = preflight(config, tmp_path, resolve_image(config.image))
    assert [f for f in findings if f.code == "federated-contract"] == []
