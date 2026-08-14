"""Unit evidence: migrations/0018_marketplace.sql:366 (ask_zc_per_hour column comment: Millicredits per machine-hour); apps/web/components/market/ListingsPanel.tsx:792 (parseZcToMzc multiplies by 1000)."""
import pytest
from flashml_cloud_api.flashml_yaml import (
    ConfigError, parse_flashml_yaml, ZC_PER_HOUR_UNIT,
)

BASE = """\
version: 2
name: routed-job
image: pytorch-2.4
entrypoint: train.py
"""


def _parse(extra: str):
    return parse_flashml_yaml(BASE + extra)


def test_absent_price_is_none_and_nothing_else_changes():
    config = _parse("")
    assert config.price is None


def test_price_parses_decimal_zc_into_the_ledger_unit():
    config = _parse("price:\n  max_per_hour: 2.00\n")
    assert config.price == {"max_zc_per_hour": 2 * ZC_PER_HOUR_UNIT}


def test_objective_is_refused_as_an_unenforced_knob():
    """Final review I2: `objective` is not wired to any real behavior in
    Phase 1 (objective-driven plan selection is Phase 2), so it is refused
    outright when present — never parsed, defaulted, or silently ignored —
    whether or not the value itself would have been a legal one."""
    with pytest.raises(ConfigError, match="objective") as exc_info:
        _parse("price:\n  max_per_hour: 1.5\n  objective: cheapest\n")
    message = str(exc_info.value)
    assert "Phase 2" in message or "not yet" in message


def test_zero_and_negative_and_absent_max_are_refused():
    with pytest.raises(ConfigError, match="max_per_hour"):
        _parse("price: {}\n")
    with pytest.raises(ConfigError, match="max_per_hour"):
        _parse("price:\n  max_per_hour: 0\n")
    with pytest.raises(ConfigError, match="max_per_hour"):
        _parse("price:\n  max_per_hour: -1\n")


def test_budget_is_refused_as_an_unenforced_knob():
    """Same reasoning as `objective` above, and the same ruling (Ruling 1,
    2026-08-13 final review): `budget` is validated-and-stored-but-
    unenforced was the OLD Phase 1 design; the final review refused that
    outright rather than ship a cap users would reasonably believe is
    live. Enforcement needs a claim-side spend guard, which is Phase 2."""
    with pytest.raises(ConfigError, match="budget") as exc_info:
        _parse("price:\n  max_per_hour: 2.0\n  budget: 25\n")
    message = str(exc_info.value)
    assert "Phase 2" in message or "not yet" in message


def test_unknown_price_keys_are_refused_by_name():
    with pytest.raises(ConfigError, match="rented"):
        _parse("price:\n  max_per_hour: 2.0\n  rented: allow\n")


def test_precision_loss_is_refused():
    """Values with more precision than 1/1000 ZC are refused, not rounded.

    The ledger stores millicredits (integers). A price the user typed must
    be the price the bid carries — no silent rounding.
    """
    # 1.2345 = 1234.5 millicredits (half millicredit, not representable)
    with pytest.raises(ConfigError, match="has more precision"):
        _parse("price:\n  max_per_hour: 1.2345\n")
    # Also refuse 0.0001 (too precise)
    with pytest.raises(ConfigError, match="has more precision"):
        _parse("price:\n  max_per_hour: 0.0001\n")


def test_exact_millicredit_boundaries_are_accepted():
    """Values representable as exact millicredits are accepted.

    1.234 = 1234 millicredits (exact)
    1.001 = 1001 millicredits (exact)
    """
    config = _parse("price:\n  max_per_hour: 1.234\n")
    assert config.price["max_zc_per_hour"] == 1234

    config = _parse("price:\n  max_per_hour: 1.001\n")
    assert config.price["max_zc_per_hour"] == 1001
