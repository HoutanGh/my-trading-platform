from __future__ import annotations

import asyncio
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

from apps.cli.repl import REPL


class _FakeConnectionConfig:
    timeout = 2.0


class _FakeConnection:
    def __init__(self, *, connected: bool = False) -> None:
        self._connected = connected
        self.config = _FakeConnectionConfig()
        self.ib = object()

    def status(self) -> dict[str, object]:
        return {"connected": self._connected}


def test_breakouts_command_registration_and_completion() -> None:
    repl = REPL(_FakeConnection())  # type: ignore[arg-type]

    assert "breakouts" in repl._commands
    assert "breakout status" not in repl._commands["breakout"].usage
    assert "breakout cancel" in repl._commands["breakout"].usage

    completion_tokens = repl._completion_candidates("breakout")
    assert "status" not in completion_tokens
    assert "list" not in completion_tokens
    assert "cancel" in completion_tokens


def test_cmd_breakouts_shows_running_watchers_table(capsys) -> None:
    repl = REPL(_FakeConnection())  # type: ignore[arg-type]

    asyncio.run(repl._cmd_breakouts([], {}))

    captured = capsys.readouterr()
    assert "No breakout watchers running." in captured.out


def test_cmd_breakouts_rejects_extra_args(capsys) -> None:
    repl = REPL(_FakeConnection())  # type: ignore[arg-type]

    asyncio.run(repl._cmd_breakouts(["AAPL"], {}))

    captured = capsys.readouterr()
    assert "breakouts" in captured.out
