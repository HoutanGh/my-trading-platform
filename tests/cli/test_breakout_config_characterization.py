from __future__ import annotations

import importlib.util
import sys
import types

if importlib.util.find_spec("ib_insync") is None:
    ib_insync_stub = types.ModuleType("ib_insync")
    ib_insync_stub.__path__ = []  # type: ignore[attr-defined]

    def _stub_type(name: str):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    ib_insync_stub.IB = _stub_type("IB")
    ib_insync_stub.Stock = _stub_type("Stock")
    ib_insync_stub.Ticker = _stub_type("Ticker")
    ib_insync_stub.BarData = _stub_type("BarData")
    ib_insync_stub.Trade = _stub_type("Trade")
    ib_insync_stub.MarketOrder = _stub_type("MarketOrder")
    ib_insync_stub.LimitOrder = _stub_type("LimitOrder")
    ib_insync_stub.StopOrder = _stub_type("StopOrder")
    ib_insync_stub.StopLimitOrder = _stub_type("StopLimitOrder")
    sys.modules["ib_insync"] = ib_insync_stub
    ib_insync_util_stub = types.ModuleType("ib_insync.util")
    ib_insync_util_stub.UNSET_DOUBLE = float("nan")
    sys.modules["ib_insync.util"] = ib_insync_util_stub

from apps.cli.repl import _deserialize_breakout_config
from apps.core.orders.models import LadderExecutionMode, OrderType


def _base_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "aapl",
        "level": 10.0,
        "qty": 10,
        "entry_type": "LIMIT",
        "use_rth": False,
        "bar_size": "1 min",
        "fast_bar_size": "1 secs",
        "fast_enabled": True,
        "tif": "DAY",
        "outside_rth": True,
        "quote_max_age_seconds": 2.0,
    }
    payload.update(overrides)
    return payload


def test_deserialize_minimal_payload_uses_attached_mode_defaults() -> None:
    config = _deserialize_breakout_config(_base_payload())

    assert config is not None
    assert config.symbol == "AAPL"
    assert config.entry_type == OrderType.LIMIT
    assert config.ladder_execution_mode == LadderExecutionMode.ATTACHED


def test_deserialize_two_tp_without_mode_infers_detached70_when_70_30_split() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
            stop_loss=9.5,
        )
    )

    assert config is not None
    assert config.take_profits == [11.0, 12.0]
    assert config.take_profit_qtys == [7, 3]
    assert config.ladder_execution_mode == LadderExecutionMode.DETACHED_70_30


def test_deserialize_two_tp_without_mode_rejects_when_70_30_split_missing() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            take_profits=[11.0, 12.0],
            stop_loss=9.5,
        )
    )

    assert config is None


def test_deserialize_three_tp_without_mode_infers_detached() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            take_profits=[11.0, 12.0, 13.0],
            stop_loss=9.5,
        )
    )

    assert config is not None
    assert config.ladder_execution_mode == LadderExecutionMode.DETACHED


def test_deserialize_detached70_requires_exact_70_30_split() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            ladder_execution_mode="detached70",
            take_profits=[11.0, 12.0],
            take_profit_qtys=[8, 2],
            stop_loss=9.5,
        )
    )

    assert config is None


def test_deserialize_detached70_accepts_mode_alias_and_valid_split() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            ladder_execution_mode="det70",
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
            stop_loss=9.5,
        )
    )

    assert config is not None
    assert config.ladder_execution_mode == LadderExecutionMode.DETACHED_70_30


def test_deserialize_detached_mode_rejects_two_tp_ladder() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            ladder_execution_mode="detached",
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
            stop_loss=9.5,
        )
    )

    assert config is None


def test_deserialize_attached_mode_rejects_tp_ladder() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            ladder_execution_mode="attached",
            take_profits=[11.0, 12.0, 13.0],
            take_profit_qtys=[6, 3, 1],
            stop_loss=9.5,
        )
    )

    assert config is None


def test_deserialize_rejects_take_profit_and_take_profits_together() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            take_profit=11.0,
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
            stop_loss=9.5,
        )
    )

    assert config is None


def test_deserialize_rejects_stop_without_take_profit() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            stop_loss=9.5,
        )
    )

    assert config is None


def test_deserialize_rejects_unknown_ladder_execution_mode() -> None:
    config = _deserialize_breakout_config(
        _base_payload(
            ladder_execution_mode="something_else",
            take_profits=[11.0, 12.0],
            take_profit_qtys=[7, 3],
            stop_loss=9.5,
        )
    )

    assert config is None
