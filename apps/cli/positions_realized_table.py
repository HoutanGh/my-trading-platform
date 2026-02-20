from __future__ import annotations

import math
from typing import Optional

from apps.core.positions.models import PositionSnapshot

_IB_UNSET_DOUBLE = 1.7976931348623157e308


def format_positions_realized_table(
    positions: list[PositionSnapshot],
    *,
    sort_desc: bool = True,
    min_realized: Optional[float] = None,
) -> list[str]:
    rows: list[PositionSnapshot] = []
    for pos in positions:
        sec_type = (pos.sec_type or "").strip().upper()
        if sec_type != "STK":
            continue
        realized = _as_finite_float(pos.realized_pnl)
        if realized is None:
            continue
        if min_realized is not None and realized < min_realized:
            continue
        rows.append(pos)

    if not rows:
        return []

    rows.sort(
        key=lambda item: (
            float(item.realized_pnl or 0.0),
            item.account or "",
            item.symbol or "",
        ),
        reverse=sort_desc,
    )

    headers = ["account", "symbol", "type", "p&l"]
    formatted_rows: list[list[str]] = []
    total_realized = 0.0
    for pos in rows:
        realized = _as_finite_float(pos.realized_pnl) or 0.0
        total_realized += realized
        formatted_rows.append(
            [
                pos.account or "-",
                pos.symbol or "-",
                pos.sec_type or "-",
                _format_number(realized),
            ]
        )

    formatted_rows.append(["TOTAL", "-", "-", _format_number(total_realized)])
    return _format_simple_table(headers, formatted_rows)


def _format_number(value: Optional[float], *, precision: int = 4) -> str:
    if value is None:
        return "-"
    try:
        formatted = f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "-"
    formatted = formatted.rstrip("0").rstrip(".")
    return formatted if formatted else "0"


def _as_finite_float(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    as_float = float(value)
    if not math.isfinite(as_float):
        return None
    if as_float == _IB_UNSET_DOUBLE:
        return None
    return as_float


def _format_simple_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    widths = [len(label) for label in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    header = " | ".join(label.ljust(widths[idx]) for idx, label in enumerate(headers))
    divider = "-+-".join("-" * width for width in widths)
    lines = [header, divider]
    for row in rows:
        lines.append(" | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))
    return lines
