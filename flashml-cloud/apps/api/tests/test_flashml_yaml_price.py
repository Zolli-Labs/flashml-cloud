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
    assert config.price == {
        "max_zc_per_hour": 2 * ZC_PER_HOUR_UNIT,
        "objective": "balanced",
        "budget_zc": None,
    }


def test_objective_is_validated_and_defaulted():
    config = _parse("price:\n  max_per_hour: 1.5\n  objective: cheapest\n")
    assert config.price["objective"] == "cheapest"
    with pytest.raises(ConfigError, match="objective"):
        _parse("price:\n  max_per_hour: 1.5\n  objective: fanciest\n")


def test_zero_and_negative_and_absent_max_are_refused():
    with pytest.raises(ConfigError, match="max_per_hour"):
        _parse("price:\n  objective: balanced\n")
    with pytest.raises(ConfigError, match="max_per_hour"):
        _parse("price:\n  max_per_hour: 0\n")
    with pytest.raises(ConfigError, match="max_per_hour"):
        _parse("price:\n  max_per_hour: -1\n")


def test_budget_must_cover_at_least_one_hour_at_the_cap():
    config = _parse("price:\n  max_per_hour: 2.0\n  budget: 25\n")
    assert config.price["budget_zc"] == 25 * ZC_PER_HOUR_UNIT
    with pytest.raises(ConfigError, match="budget"):
        _parse("price:\n  max_per_hour: 2.0\n  budget: 1\n")


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
