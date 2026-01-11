from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from apps.core.market_data.models import Bar


class BreakoutAction(str, Enum):
    ENTER = "enter"
    STOP = "stop"


@dataclass(frozen=True)
class BreakoutRuleConfig:
    level: float


@dataclass(frozen=True)
class BreakoutState:
    break_seen: bool = False
    break_bar_time: Optional[datetime] = None


def evaluate_breakout(
    state: BreakoutState,
    bar: Bar,
    config: BreakoutRuleConfig,
) -> Tuple[BreakoutState, Optional[BreakoutAction]]:
    """
    Pure breakout rule evaluation.
    - First bar with high >= level sets break_seen.
    - Next bar with open >= level triggers ENTER, otherwise STOP.
    """
    if not state.break_seen:
        if bar.high >= config.level:
            return BreakoutState(break_seen=True, break_bar_time=bar.timestamp), None
        return state, None

    if bar.open >= config.level:
        return state, BreakoutAction.ENTER
    return state, BreakoutAction.STOP
