from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from typing import Any

_BACKEND_CANDIDATES = ("ib_async", "in_async", "ib_insync")
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

try:
    _util_module = import_module(f"{_backend_name}.util")
except Exception:
    _util_module = SimpleNamespace()
_backend_parse_datetime = getattr(_util_module, "parseIBDatetime", None)
if _backend_parse_datetime is None:
    _backend_parse_datetime = getattr(_util_module, "parse_ib_datetime", None)


def _missing_backend_type(name: str) -> type:
    return type(name, (), {"__init__": lambda self, *args, **kwargs: None})


def _backend_attr(name: str) -> Any:
    attr = getattr(_backend, name, None)
    if attr is None:
        return _missing_backend_type(name)
    return attr


IB = _backend_attr("IB")
BarData = _backend_attr("BarData")
IB_CLIENT_BACKEND = _backend_name
LimitOrder = _backend_attr("LimitOrder")
MarketOrder = _backend_attr("MarketOrder")
Stock = _backend_attr("Stock")
StopLimitOrder = _backend_attr("StopLimitOrder")
StopOrder = _backend_attr("StopOrder")
Ticker = _backend_attr("Ticker")
Trade = _backend_attr("Trade")
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
