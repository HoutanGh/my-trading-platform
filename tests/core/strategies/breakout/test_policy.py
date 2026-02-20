from __future__ import annotations

import pytest

from apps.core.orders.models import LadderExecutionMode
from apps.core.strategies.breakout.policy import (
    default_take_profit_ratios,
    expected_detached70_qtys,
    infer_ladder_execution_mode,
    ladder_execution_mode_label,
    parse_ladder_execution_mode,
    parse_take_profit_ratios,
    split_qty_by_ratios,
    split_take_profit_qtys,
    stop_updates_for_take_profits,
    validate_ladder_execution_mode,
    validate_take_profit_levels,
)


def test_parse_ladder_execution_mode_accepts_defaults_and_aliases() -> None:
    assert parse_ladder_execution_mode(None) == LadderExecutionMode.ATTACHED
    assert parse_ladder_execution_mode("") == LadderExecutionMode.ATTACHED
    assert parse_ladder_execution_mode("attached") == LadderExecutionMode.ATTACHED
    assert parse_ladder_execution_mode("detached") == LadderExecutionMode.DETACHED
    assert parse_ladder_execution_mode("det") == LadderExecutionMode.DETACHED
    assert parse_ladder_execution_mode("detached70") == LadderExecutionMode.DETACHED_70_30
    assert parse_ladder_execution_mode("det70") == LadderExecutionMode.DETACHED_70_30
    assert parse_ladder_execution_mode("detached_70_30") == LadderExecutionMode.DETACHED_70_30
    assert (
        parse_ladder_execution_mode(LadderExecutionMode.DETACHED_70_30)
        == LadderExecutionMode.DETACHED_70_30
    )


def test_parse_ladder_execution_mode_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="invalid ladder execution mode"):
        parse_ladder_execution_mode("unknown")


def test_ladder_execution_mode_label_is_stable() -> None:
    assert ladder_execution_mode_label(LadderExecutionMode.ATTACHED) == "attached"
    assert ladder_execution_mode_label(LadderExecutionMode.DETACHED) == "detached"
    assert ladder_execution_mode_label(LadderExecutionMode.DETACHED_70_30) == "detached70"


def test_infer_mode_prefers_explicit_mode_when_provided() -> None:
    mode = infer_ladder_execution_mode(
        mode_value="detached",
        qty=10,
        take_profits=[11.0, 12.0],
        take_profit_qtys=[7, 3],
    )
    assert mode == LadderExecutionMode.DETACHED


def test_infer_mode_with_two_tp_detects_detached70_by_qty_split() -> None:
    mode = infer_ladder_execution_mode(
        mode_value=None,
        qty=10,
        take_profits=[11.0, 12.0],
        take_profit_qtys=[7, 3],
    )
    assert mode == LadderExecutionMode.DETACHED_70_30


def test_infer_mode_with_two_tp_non_70_30_falls_back_to_detached() -> None:
    mode = infer_ladder_execution_mode(
        mode_value=None,
        qty=10,
        take_profits=[11.0, 12.0],
        take_profit_qtys=[8, 2],
    )
    assert mode == LadderExecutionMode.DETACHED


def test_infer_mode_with_three_tp_defaults_to_detached() -> None:
    mode = infer_ladder_execution_mode(
        mode_value=None,
        qty=10,
        take_profits=[11.0, 12.0, 13.0],
        take_profit_qtys=[6, 3, 1],
    )
    assert mode == LadderExecutionMode.DETACHED


def test_infer_mode_without_ladder_defaults_to_attached() -> None:
    mode = infer_ladder_execution_mode(
        mode_value=None,
        qty=10,
        take_profits=None,
        take_profit_qtys=None,
    )
    assert mode == LadderExecutionMode.ATTACHED


def test_validate_take_profit_levels() -> None:
    assert validate_take_profit_levels([1.0, 1.2, 1.5])
    assert not validate_take_profit_levels([])
    assert not validate_take_profit_levels([1.0, 1.0])
    assert not validate_take_profit_levels([1.1, 1.0])
    assert not validate_take_profit_levels([1.0, -1.0])


def test_default_take_profit_ratios_by_count() -> None:
    assert default_take_profit_ratios(1) == [1.0]
    assert default_take_profit_ratios(2) == [0.7, 0.3]
    assert default_take_profit_ratios(3) == [0.6, 0.3, 0.1]
    with pytest.raises(ValueError, match="count must be 1, 2, or 3"):
        default_take_profit_ratios(4)


def test_parse_take_profit_ratios_handles_percent_style_and_decimal_style() -> None:
    assert parse_take_profit_ratios("70-30", 2) == [0.7, 0.3]
    assert parse_take_profit_ratios("60-30-10", 3) == [0.6, 0.3, 0.1]
    assert parse_take_profit_ratios("0.7-0.3", 2) == [0.7, 0.3]


def test_parse_take_profit_ratios_rejects_invalid_shapes_or_values() -> None:
    assert parse_take_profit_ratios(None, 2) is None
    assert parse_take_profit_ratios("", 2) is None
    assert parse_take_profit_ratios("70-30", 3) is None
    assert parse_take_profit_ratios("0-1", 2) is None
    assert parse_take_profit_ratios("abc-def", 2) is None
    assert parse_take_profit_ratios("0.2-0.2", 2) is None


def test_split_qty_by_ratios_rounding_and_errors() -> None:
    assert split_qty_by_ratios(10, [0.7, 0.3]) == [7, 3]
    assert split_qty_by_ratios(7, [0.6, 0.3, 0.1]) == [4, 2, 1]

    with pytest.raises(ValueError, match="qty must be greater than zero"):
        split_qty_by_ratios(0, [0.7, 0.3])
    with pytest.raises(ValueError, match="ratios are required"):
        split_qty_by_ratios(10, [])
    with pytest.raises(ValueError, match="ratios must be positive"):
        split_qty_by_ratios(10, [0.7, -0.3])
    with pytest.raises(ValueError, match="qty too small for requested tp allocation"):
        split_qty_by_ratios(2, [0.6, 0.3, 0.1])


def test_split_take_profit_qtys_uses_default_ratios() -> None:
    assert split_take_profit_qtys(10, 2) == [7, 3]
    assert split_take_profit_qtys(10, 3) == [6, 3, 1]
    with pytest.raises(ValueError, match="qty too small for requested tp allocation"):
        split_take_profit_qtys(2, 3)


def test_stop_updates_for_take_profits() -> None:
    assert stop_updates_for_take_profits([11.0, 12.0], breakout_level=10.0) == [10.0]
    assert stop_updates_for_take_profits([11.0, 12.0, 13.0], breakout_level=10.0) == [10.0, 11.0]
    with pytest.raises(ValueError, match="only 2 or 3 take profits are supported"):
        stop_updates_for_take_profits([11.0], breakout_level=10.0)


def test_expected_detached70_qtys() -> None:
    assert expected_detached70_qtys(10) == [7, 3]
    assert expected_detached70_qtys(100) == [70, 30]


def test_validate_ladder_execution_mode_enforces_matrix() -> None:
    with pytest.raises(ValueError, match="ATTACHED ladder mode is not supported"):
        validate_ladder_execution_mode(
            mode=LadderExecutionMode.ATTACHED,
            qty=10,
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
        )
    with pytest.raises(ValueError, match="DETACHED requires exactly 3 take_profits"):
        validate_ladder_execution_mode(
            mode=LadderExecutionMode.DETACHED,
            qty=10,
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
        )
    with pytest.raises(ValueError, match="DETACHED_70_30 requires exactly 2 take_profits"):
        validate_ladder_execution_mode(
            mode=LadderExecutionMode.DETACHED_70_30,
            qty=10,
            take_profits=[11.0, 12.0, 13.0],
            take_profit_qtys=[6, 3, 1],
        )
    with pytest.raises(ValueError, match=r"DETACHED_70_30 requires take_profit_qtys=\[7, 3\]"):
        validate_ladder_execution_mode(
            mode=LadderExecutionMode.DETACHED_70_30,
            qty=10,
            take_profits=[11.0, 12.0],
            take_profit_qtys=[8, 2],
        )

    validate_ladder_execution_mode(
        mode=LadderExecutionMode.DETACHED_70_30,
        qty=10,
        take_profits=[11.0, 12.0],
        take_profit_qtys=[7, 3],
    )
    validate_ladder_execution_mode(
        mode=LadderExecutionMode.DETACHED,
        qty=10,
        take_profits=[11.0, 12.0, 13.0],
        take_profit_qtys=[6, 3, 1],
    )
