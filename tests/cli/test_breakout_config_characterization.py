from __future__ import annotations

import importlib
from datetime import datetime, timezone
import sys
import types

_IB_CLIENT_MODULE = "apps.adapters.broker._ib_client"

try:
    importlib.import_module(_IB_CLIENT_MODULE)
except Exception:
    ib_client_stub = types.ModuleType(_IB_CLIENT_MODULE)

    def _stub_type(name: str):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    def _parse_ib_datetime(_value: object) -> datetime:
        return datetime.now(timezone.utc)

    ib_client_stub.IB = _stub_type("IB")
    ib_client_stub.Stock = _stub_type("Stock")
    ib_client_stub.Ticker = _stub_type("Ticker")
    ib_client_stub.BarData = _stub_type("BarData")
    ib_client_stub.Trade = _stub_type("Trade")
    ib_client_stub.MarketOrder = _stub_type("MarketOrder")
    ib_client_stub.LimitOrder = _stub_type("LimitOrder")
    ib_client_stub.StopOrder = _stub_type("StopOrder")
    ib_client_stub.StopLimitOrder = _stub_type("StopLimitOrder")
    ib_client_stub.IB_CLIENT_BACKEND = "test-stub"
    ib_client_stub.UNSET_DOUBLE = float("nan")
    ib_client_stub.parse_ib_datetime = _parse_ib_datetime
    sys.modules[_IB_CLIENT_MODULE] = ib_client_stub

from apps.cli.repl import _breakout_mode_validation_error_text, _deserialize_breakout_config
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


def test_breakout_mode_error_text_mapping_for_cli_messages() -> None:
    assert _breakout_mode_validation_error_text(
        LadderExecutionMode.ATTACHED,
        "ATTACHED ladder mode is not supported; use bracket for 1 TP + 1 SL",
    ) == "tp_exec=attached supports only single tp/sl (use tp=LEVEL, not a ladder)."
    assert _breakout_mode_validation_error_text(
        LadderExecutionMode.DETACHED,
        "DETACHED requires exactly 3 take_profits",
    ) == "tp_exec=detached requires a 3-level tp ladder with sl."
    assert _breakout_mode_validation_error_text(
        LadderExecutionMode.DETACHED_70_30,
        "DETACHED_70_30 requires exactly 2 take_profits",
    ) == "tp_exec=detached70 requires a 2-level tp ladder with sl (tp=LEVEL1-LEVEL2)."
    assert _breakout_mode_validation_error_text(
        LadderExecutionMode.DETACHED_70_30,
        "DETACHED_70_30 requires take_profit_qtys=[7, 3]",
    ) == "tp_alloc must be exactly 70-30 when tp_exec=detached70"
    assert _breakout_mode_validation_error_text(
        LadderExecutionMode.DETACHED_70_30,
        "unexpected",
    ) == "unexpected"
