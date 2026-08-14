"""`placement:` — the submitter's say over WHERE a priced job may run.

The yaml layer validates SHAPE and nothing else. Whether ``cpu-large`` is a
real capability class, whether it is consistent with ``resources.gpus``, and
what order the router walks the list in are all decided in
``flashml_cloud_api.routing`` (see ``tests/test_routing.py``) — this module
must never import ``marketplace``, so it cannot know the ladder and does not
pretend to. What it CAN say is that ``accept`` is a non-empty list of
non-empty strings with no repeats, and that is what these tests pin.

Ordering is load-bearing and therefore asserted as a list, not a set: the
router walks ``accept`` in the order it was written and stops when the tasks
run out, so re-ordering the list changes which classes get a bid.
"""
import pytest

from flashml_cloud_api.flashml_yaml import ConfigError, parse_flashml_yaml

BASE = """\
version: 2
name: placed-job
image: pytorch-2.4
entrypoint: train.py
"""


def _parse(extra: str):
    return parse_flashml_yaml(BASE + extra)


def test_absent_placement_is_none():
    assert _parse("").placement is None


def test_accept_parses_in_the_order_written():
    config = _parse(
        "placement:\n  accept:\n    - gpu-8gb\n    - gpu-16gb\n    - cpu-large\n"
    )
    assert config.placement == {"accept": ["gpu-8gb", "gpu-16gb", "cpu-large"]}


def test_a_reordered_accept_is_a_different_config():
    """Order is the routing walk order, so it survives parsing verbatim
    rather than being normalised, sorted or de-duplicated into a set."""
    first = _parse("placement:\n  accept:\n    - gpu-16gb\n    - gpu-8gb\n")
    second = _parse("placement:\n  accept:\n    - gpu-8gb\n    - gpu-16gb\n")
    assert first.placement != second.placement


def test_placement_without_price_still_parses():
    """`placement:` alone is legal at this layer. Whether it ROUTES is the
    submit path's business — only priced jobs route — and refusing it here
    would refuse a perfectly readable config for a reason this module has no
    standing to know."""
    config = _parse("placement:\n  accept:\n    - cpu-small\n")
    assert config.price is None
    assert config.placement == {"accept": ["cpu-small"]}


def test_placement_with_price_parses_both():
    config = _parse(
        "price:\n  max_per_hour: 2.0\n"
        "placement:\n  accept:\n    - cpu-large\n"
    )
    assert config.price == {"max_zc_per_hour": 2000, "objective": "balanced"}
    assert config.placement == {"accept": ["cpu-large"]}


def test_an_empty_accept_list_is_refused():
    with pytest.raises(ConfigError, match="accept"):
        _parse("placement:\n  accept: []\n")


def test_a_placement_block_with_no_accept_is_refused():
    """Nothing else is allowed in `placement:` yet, so a block without
    `accept` says nothing at all — refused rather than parsed into an empty
    dict a reader would take for a constraint."""
    with pytest.raises(ConfigError, match="accept"):
        _parse("placement: {}\n")


def test_accept_must_be_a_list_not_a_bare_string():
    with pytest.raises(ConfigError, match="accept"):
        _parse("placement:\n  accept: cpu-large\n")


def test_accept_entries_must_be_non_empty_strings():
    with pytest.raises(ConfigError, match="accept"):
        _parse("placement:\n  accept:\n    - ''\n")
    with pytest.raises(ConfigError, match="accept"):
        _parse("placement:\n  accept:\n    - 4\n")
    with pytest.raises(ConfigError, match="accept"):
        _parse("placement:\n  accept:\n    - [cpu-small]\n")


def test_duplicate_accept_entries_are_refused_by_name():
    """A repeat is refused rather than collapsed: the router walks the list
    once per entry and re-reads the SAME open book each time, so a class
    named twice would count one listing's capacity twice."""
    with pytest.raises(ConfigError, match="cpu-small") as exc_info:
        _parse("placement:\n  accept:\n    - cpu-small\n    - cpu-large\n    - cpu-small\n")
    assert "duplicate" in str(exc_info.value).lower()


def test_placement_must_be_a_mapping():
    with pytest.raises(ConfigError, match="placement"):
        _parse("placement:\n  - cpu-small\n")


def test_countries_is_refused_as_a_known_future_key():
    with pytest.raises(ConfigError, match="countries") as exc_info:
        _parse("placement:\n  accept:\n    - cpu-small\n  countries:\n    - DE\n")
    assert "not supported yet" in str(exc_info.value)


def test_pool_is_refused_and_points_at_where_it_actually_lives():
    """`pool` is not a future key — it exists today, at submit, as a request
    parameter. Refusing it by name with that pointer is the difference
    between a user moving one line and a user deleting a constraint they
    still want."""
    with pytest.raises(ConfigError, match="pool") as exc_info:
        _parse("placement:\n  accept:\n    - cpu-small\n  pool: my-team\n")
    assert "submit" in str(exc_info.value)


def test_unknown_placement_keys_are_refused_by_name():
    with pytest.raises(ConfigError, match="region"):
        _parse("placement:\n  accept:\n    - cpu-small\n  region: eu-west\n")


def test_placement_is_an_allowed_top_level_key():
    """Regression guard for the wiring itself: without `placement` in
    OPTIONAL_KEYS the generic unknown-key check refuses the block before
    `_validate_placement` ever sees it, and the error names the wrong
    problem."""
    from flashml_cloud_api.flashml_yaml import ALLOWED_KEYS, OPTIONAL_KEYS

    assert "placement" in OPTIONAL_KEYS
    assert "placement" in ALLOWED_KEYS
