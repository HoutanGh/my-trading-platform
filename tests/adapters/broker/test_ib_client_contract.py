from __future__ import annotations

import importlib.util
from pathlib import Path
import types
from typing import Any


def _ib_client_path() -> Path:
    return Path(__file__).resolve().parents[3] / "apps" / "adapters" / "broker" / "_ib_client.py"


def _load_module_from_path(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_backend_module(*, include_trade: bool) -> types.ModuleType:
    module = types.ModuleType("ib_async")

    def _stub_type(name: str):
        return type(name, (), {"__init__": lambda self, *args, **kwargs: None})

    module.IB = _stub_type("IB")
    module.BarData = _stub_type("BarData")
    module.LimitOrder = _stub_type("LimitOrder")
    module.MarketOrder = _stub_type("MarketOrder")
    module.Stock = _stub_type("Stock")
    module.StopLimitOrder = _stub_type("StopLimitOrder")
    module.StopOrder = _stub_type("StopOrder")
    module.Ticker = _stub_type("Ticker")
    if include_trade:
        module.Trade = _stub_type("Trade")
    return module


def _stub_backend_util_module() -> types.ModuleType:
    module = types.ModuleType("ib_async.util")
    module.UNSET_DOUBLE = float("nan")
    module.parseIBDatetime = lambda _value: None  # type: ignore[assignment]
    return module


def test_ib_client_import_fails_fast_when_required_symbol_missing(monkeypatch: Any) -> None:
    monkeypatch.setitem(__import__("sys").modules, "ib_async", _stub_backend_module(include_trade=False))
    monkeypatch.setitem(__import__("sys").modules, "ib_async.util", _stub_backend_util_module())
    monkeypatch.setitem(__import__("sys").modules, "ib_insync", None)
    monkeypatch.setitem(__import__("sys").modules, "ib_insync.util", None)

    try:
        _load_module_from_path(_ib_client_path(), "apps.adapters.broker._ib_client_contract_missing")
        assert False, "Expected ImportError for missing backend symbols"
    except ImportError as exc:
        message = str(exc)
        assert "missing required symbols" in message
        assert "Trade" in message


def test_ib_client_import_succeeds_when_required_symbols_exist(monkeypatch: Any) -> None:
    monkeypatch.setitem(__import__("sys").modules, "ib_async", _stub_backend_module(include_trade=True))
    monkeypatch.setitem(__import__("sys").modules, "ib_async.util", _stub_backend_util_module())
    monkeypatch.setitem(__import__("sys").modules, "ib_insync", None)
    monkeypatch.setitem(__import__("sys").modules, "ib_insync.util", None)

    module = _load_module_from_path(_ib_client_path(), "apps.adapters.broker._ib_client_contract_ok")
    assert getattr(module, "IB_CLIENT_BACKEND", None) == "ib_async"
    assert hasattr(module, "IB")
    assert hasattr(module, "Trade")
