from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from typing import Any

_BACKEND_CANDIDATES = ("ib_async", "ib_insync")
_REQUIRED_SYMBOLS = (
    "IB",
    "BarData",
    "LimitOrder",
    "MarketOrder",
    "Stock",
    "StopLimitOrder",
    "StopOrder",
    "Ticker",
    "Trade",
)
_LAST_IMPORT_ERROR: Exception | None = None
_backend: Any | None = None
_backend_name = ""

for candidate in _BACKEND_CANDIDATES:
    try:
        _backend = import_module(candidate)
        _backend_name = candidate
        break
    except Exception as exc:
        _LAST_IMPORT_ERROR = exc

if _backend is None:
    raise ModuleNotFoundError(
        "Could not import an IB client backend. Install one of: "
        + ", ".join(_BACKEND_CANDIDATES)
    ) from _LAST_IMPORT_ERROR


_missing_symbols = [name for name in _REQUIRED_SYMBOLS if not hasattr(_backend, name)]
if _missing_symbols:
    raise ImportError(
        f"Backend '{_backend_name}' is missing required symbols: {', '.join(_missing_symbols)}"
    )


try:
    _util_module = import_module(f"{_backend_name}.util")
except Exception:
    _util_module = SimpleNamespace()
_backend_parse_datetime = getattr(_util_module, "parseIBDatetime", None)
if _backend_parse_datetime is None:
    _backend_parse_datetime = getattr(_util_module, "parse_ib_datetime", None)

IB = _backend.IB
BarData = _backend.BarData
IB_CLIENT_BACKEND = _backend_name
LimitOrder = _backend.LimitOrder
MarketOrder = _backend.MarketOrder
Stock = _backend.Stock
StopLimitOrder = _backend.StopLimitOrder
StopOrder = _backend.StopOrder
Ticker = _backend.Ticker
Trade = _backend.Trade
UNSET_DOUBLE = getattr(_util_module, "UNSET_DOUBLE", 1.7976931348623157e308)


def parse_ib_datetime(value: object) -> datetime:
    if callable(_backend_parse_datetime):
        try:
            parsed = _backend_parse_datetime(value)
            if isinstance(parsed, datetime):
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed
        except Exception:
            pass

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    text = str(value).strip()
    if not text:
        return datetime.now(timezone.utc)

    normalized = text.replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 8:
            year = int(digits[:4])
            month = int(digits[4:6])
            day = int(digits[6:8])
            hour = int(digits[8:10]) if len(digits) >= 10 else 0
            minute = int(digits[10:12]) if len(digits) >= 12 else 0
            second = int(digits[12:14]) if len(digits) >= 14 else 0
            try:
                return datetime(
                    year,
                    month,
                    day,
                    hour,
                    minute,
                    second,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                pass
    return datetime.now(timezone.utc)


__all__ = [
    "IB",
    "BarData",
    "IB_CLIENT_BACKEND",
    "LimitOrder",
    "MarketOrder",
    "Stock",
    "StopLimitOrder",
    "StopOrder",
    "Ticker",
    "Trade",
    "UNSET_DOUBLE",
    "parse_ib_datetime",
]
