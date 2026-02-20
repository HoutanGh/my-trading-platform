from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, MutableSequence, Sequence


@dataclass(frozen=True)
class DetachedRepriceMilestone:
    required_pairs: frozenset[int]
    target_pairs: tuple[int, ...]
    stop_price: float


@dataclass(frozen=True)
class DetachedRepriceDecision:
    target_pairs: tuple[int, ...]
    stop_price: float


def collect_detached_reprice_decisions(
    *,
    tp_completed: Mapping[int, bool],
    milestones: Sequence[DetachedRepriceMilestone],
    milestone_applied: MutableSequence[bool],
) -> list[DetachedRepriceDecision]:
    if len(milestones) != len(milestone_applied):
        raise ValueError("milestone_applied must match milestones")
    completed_pairs = {idx for idx, done in tp_completed.items() if done}
    decisions: list[DetachedRepriceDecision] = []
    for milestone_idx, milestone in enumerate(milestones):
        if milestone_applied[milestone_idx]:
            continue
        if not milestone.required_pairs.issubset(completed_pairs):
            continue
        milestone_applied[milestone_idx] = True
        decisions.append(
            DetachedRepriceDecision(
                target_pairs=milestone.target_pairs,
                stop_price=milestone.stop_price,
            )
        )
    return decisions


def select_detached_incident_pair(
    *,
    order_id_value: int,
    code: int,
    leg_by_order_id: Mapping[int, tuple[str, int]],
    incident_pairs_active: set[int],
    tp_completed: Mapping[int, bool],
    stop_filled: Mapping[int, bool],
    tp_has_fill: Callable[[int], bool],
    stop_has_fill: Callable[[int], bool],
) -> int | None:
    leg = leg_by_order_id.get(order_id_value)
    if leg is None:
        return None
    leg_kind, pair_index = leg
    if pair_index in incident_pairs_active:
        return None
    if code in {201, 404}:
        return pair_index
    if code != 202:
        return None
    if leg_kind == "stop":
        if tp_completed.get(pair_index, False) or tp_has_fill(pair_index):
            return None
    if leg_kind == "tp":
        if stop_filled.get(pair_index, False) or stop_has_fill(pair_index):
            return None
    return pair_index
