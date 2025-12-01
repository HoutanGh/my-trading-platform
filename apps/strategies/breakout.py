from dataclasses import dataclass
from typing import Optional, Tuple, Any


@dataclass
class BreakoutState:
    """State for the simple breakout logic."""
    break_seen: bool = False


def evaluate_breakout(state: BreakoutState, bar: Any, level: float) -> Tuple[BreakoutState, Optional[str]]:
    """
    Given the current state and a new 1m bar, decide what to do.
    Returns (new_state, action) where action is one of:
      - None: keep waiting
      - "enter": confirm candle hit; go place the order
      - "stop": confirm failed; stop without trading
    """
    # First time we see a bar with high >= level, record the break.
    if not state.break_seen:
        if getattr(bar, "high", 0) >= level:
            return BreakoutState(break_seen=True), None
        return state, None

    # This is the next bar after a break; treat it as the confirm candidate.
    if getattr(bar, "open", 0) >= level:
        return state, "enter"
    return state, "stop"
