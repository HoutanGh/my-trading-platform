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
    - First bar with close >= level triggers ENTER.
    - Otherwise keep waiting.
    """
    if bar.close >= config.level:
        return BreakoutState(break_seen=True, break_bar_time=bar.timestamp), BreakoutAction.ENTER
    return state, None
