from __future__ import annotations

import asyncio

import pytest

from apps.core.orders.models import LadderExecutionMode, OrderType
from apps.core.strategies.breakout.logic import BreakoutRuleConfig
from apps.core.strategies.breakout.runner import (
    BreakoutRunConfig,
    _default_breakout_tag,
    run_breakout,
)


def _run(coro):
    return asyncio.run(coro)


def _base_config(**overrides) -> BreakoutRunConfig:
    config = BreakoutRunConfig(
        symbol="AAPL",
        qty=10,
        rule=BreakoutRuleConfig(level=10.0),
        entry_type=OrderType.MARKET,
    )
    values = {**config.__dict__, **overrides}
    return BreakoutRunConfig(**values)


def test_default_breakout_tag_format_is_stable() -> None:
    assert _default_breakout_tag("AAPL", 190.0) == "breakout:AAPL:190"
    assert _default_breakout_tag("RIME", 1.25) == "breakout:RIME:1.25"


def test_take_profit_and_stop_loss_must_be_provided_together() -> None:
    config = _base_config(take_profit=10.8, stop_loss=None)

    with pytest.raises(ValueError, match="take_profit and stop_loss must be provided together"):
        _run(run_breakout(config, bar_stream=None, order_service=None))


def test_take_profit_and_take_profits_are_mutually_exclusive() -> None:
    config = _base_config(
        take_profit=10.8,
        take_profits=[10.8, 11.2],
        stop_loss=9.5,
    )

    with pytest.raises(ValueError, match="take_profit and take_profits cannot both be provided"):
        _run(run_breakout(config, bar_stream=None, order_service=None))


def test_limit_entry_requires_quote_source() -> None:
    config = _base_config(entry_type=OrderType.LIMIT)

    with pytest.raises(ValueError, match="quote_port is required for limit breakout entries"):
        _run(run_breakout(config, bar_stream=None, order_service=None))


def test_take_profit_qtys_must_sum_to_qty() -> None:
    config = _base_config(
        take_profits=[11.0, 11.5],
        take_profit_qtys=[3, 3],
        stop_loss=9.5,
    )

    with pytest.raises(ValueError, match="take_profit_qtys must sum to qty"):
        _run(run_breakout(config, bar_stream=None, order_service=None))


def test_detached70_requires_exactly_two_take_profits() -> None:
    config = _base_config(
        take_profits=[11.0, 11.5, 12.0],
        take_profit_qtys=[6, 3, 1],
        stop_loss=9.5,
        ladder_execution_mode=LadderExecutionMode.DETACHED_70_30,
    )

    with pytest.raises(ValueError, match="DETACHED_70_30 requires exactly 2 take_profits"):
        _run(run_breakout(config, bar_stream=None, order_service=None))

