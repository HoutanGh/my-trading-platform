from __future__ import annotations

from typing import Sequence

from apps.core.orders.models import LadderExecutionMode


def parse_ladder_execution_mode(value: object) -> LadderExecutionMode:
    if value is None:
        return LadderExecutionMode.ATTACHED
    if isinstance(value, LadderExecutionMode):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "attached"}:
            return LadderExecutionMode.ATTACHED
        if normalized in {"detached", "det"}:
            return LadderExecutionMode.DETACHED
        if normalized in {"detached70", "det70", "detached_70_30"}:
            return LadderExecutionMode.DETACHED_70_30
    raise ValueError("invalid ladder execution mode")


def ladder_execution_mode_label(mode: LadderExecutionMode) -> str:
    if mode == LadderExecutionMode.DETACHED:
        return "detached"
    if mode == LadderExecutionMode.DETACHED_70_30:
        return "detached70"
    return "attached"


def infer_ladder_execution_mode(
    *,
    mode_value: object = None,
    qty: int,
    take_profits: Sequence[float] | None,
    take_profit_qtys: Sequence[int] | None,
) -> LadderExecutionMode:
    if mode_value is not None:
        return parse_ladder_execution_mode(mode_value)
    if not take_profits:
        return LadderExecutionMode.ATTACHED
    if len(take_profits) == 2:
        expected_two = expected_detached70_qtys(qty)
        if list(take_profit_qtys or []) == expected_two:
            return LadderExecutionMode.DETACHED_70_30
        return LadderExecutionMode.DETACHED
    if len(take_profits) == 3:
        return LadderExecutionMode.DETACHED
    return LadderExecutionMode.ATTACHED


def validate_take_profit_levels(levels: Sequence[float]) -> bool:
    if not levels:
        return False
    if any(level <= 0 for level in levels):
        return False
    for idx in range(1, len(levels)):
        if levels[idx] <= levels[idx - 1]:
            return False
    return True


def default_take_profit_ratios(count: int) -> list[float]:
    if count == 1:
        return [1.0]
    if count == 2:
        return [0.7, 0.3]
    if count == 3:
        return [0.6, 0.3, 0.1]
    raise ValueError("count must be 1, 2, or 3")


def parse_take_profit_ratios(value: object, expected_count: int) -> list[float] | None:
    if value is None:
        return None
    text = _coerce_str(value)
    if not text:
        return None
    parts = [part.strip() for part in text.split("-") if part.strip()]
    if len(parts) != expected_count:
        return None
    ratios: list[float] = []
    for part in parts:
        try:
            parsed = float(part)
        except ValueError:
            return None
        ratios.append(parsed)
    if any(ratio <= 0 for ratio in ratios):
        return None
    total = sum(ratios)
    if total <= 0:
        return None
    if total > 1.5:
        return [ratio / total for ratio in ratios]
    if abs(total - 1.0) > 0.05:
        return None
    return ratios


def split_qty_by_ratios(total_qty: int, ratios: Sequence[float]) -> list[int]:
    if total_qty <= 0:
        raise ValueError("qty must be greater than zero")
    if not ratios:
        raise ValueError("ratios are required")
    if any(ratio <= 0 for ratio in ratios):
        raise ValueError("ratios must be positive")
    total = sum(ratios)
    if total <= 0:
        raise ValueError("ratios sum must be positive")
    normalized = [ratio / total for ratio in ratios]
    raw = [total_qty * ratio for ratio in normalized]
    qtys = [int(value) for value in raw]
    remainder = total_qty - sum(qtys)
    if remainder > 0:
        fractions = [(idx, raw[idx] - qtys[idx]) for idx in range(len(qtys))]
        fractions.sort(key=lambda item: (-item[1], item[0]))
        for idx, _fraction in fractions[:remainder]:
            qtys[idx] += 1
    if any(qty <= 0 for qty in qtys):
        raise ValueError("qty too small for requested tp allocation")
    return qtys


def split_take_profit_qtys(total_qty: int, count: int) -> list[int]:
    ratios = default_take_profit_ratios(count)
    return split_qty_by_ratios(total_qty, ratios)


def stop_updates_for_take_profits(take_profits: Sequence[float], breakout_level: float) -> list[float]:
    if len(take_profits) == 2:
        return [breakout_level]
    if len(take_profits) == 3:
        return [breakout_level, take_profits[0]]
    raise ValueError("only 2 or 3 take profits are supported")


def expected_detached70_qtys(total_qty: int) -> list[int]:
    return split_qty_by_ratios(total_qty, [0.7, 0.3])


def validate_ladder_execution_mode(
    *,
    mode: LadderExecutionMode,
    qty: int,
    take_profits: Sequence[float] | None,
    take_profit_qtys: Sequence[int] | None,
) -> None:
    if mode == LadderExecutionMode.ATTACHED and take_profits:
        raise ValueError("ATTACHED ladder mode is not supported; use bracket for 1 TP + 1 SL")
    if mode == LadderExecutionMode.DETACHED:
        if not take_profits or len(take_profits) != 3:
            raise ValueError("DETACHED requires exactly 3 take_profits")
    if mode == LadderExecutionMode.DETACHED_70_30:
        if not take_profits or len(take_profits) != 2:
            raise ValueError("DETACHED_70_30 requires exactly 2 take_profits")
        expected_qtys = expected_detached70_qtys(qty)
        if list(take_profit_qtys or []) != expected_qtys:
            raise ValueError(f"DETACHED_70_30 requires take_profit_qtys={expected_qtys}")


def _coerce_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
