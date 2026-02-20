from __future__ import annotations

import pytest

from apps.core.orders.detached_ladder import (
    DetachedRepriceMilestone,
    collect_detached_reprice_decisions,
    select_detached_incident_pair,
)


def test_collect_detached_reprice_decisions_applies_milestones_once() -> None:
    milestones = (
        DetachedRepriceMilestone(
            required_pairs=frozenset({1}),
            target_pairs=(2,),
            stop_price=10.0,
        ),
        DetachedRepriceMilestone(
            required_pairs=frozenset({1, 2}),
            target_pairs=(3,),
            stop_price=11.0,
        ),
    )
    applied = [False, False]

    first = collect_detached_reprice_decisions(
        tp_completed={1: True, 2: False, 3: False},
        milestones=milestones,
        milestone_applied=applied,
    )
    assert [item.target_pairs for item in first] == [(2,)]
    assert [item.stop_price for item in first] == [10.0]
    assert applied == [True, False]

    repeat = collect_detached_reprice_decisions(
        tp_completed={1: True, 2: False, 3: False},
        milestones=milestones,
        milestone_applied=applied,
    )
    assert repeat == []
    assert applied == [True, False]

    second = collect_detached_reprice_decisions(
        tp_completed={1: True, 2: True, 3: False},
        milestones=milestones,
        milestone_applied=applied,
    )
    assert [item.target_pairs for item in second] == [(3,)]
    assert [item.stop_price for item in second] == [11.0]
    assert applied == [True, True]


def test_collect_detached_reprice_decisions_rejects_bad_applied_length() -> None:
    milestones = (
        DetachedRepriceMilestone(
            required_pairs=frozenset({1}),
            target_pairs=(2,),
            stop_price=10.0,
        ),
    )
    with pytest.raises(ValueError, match="milestone_applied must match milestones"):
        collect_detached_reprice_decisions(
            tp_completed={1: True},
            milestones=milestones,
            milestone_applied=[],
        )


def test_select_detached_incident_pair_core_rules() -> None:
    leg_map = {
        101: ("stop", 1),
        102: ("tp", 2),
    }

    assert (
        select_detached_incident_pair(
            order_id_value=999,
            code=201,
            leg_by_order_id=leg_map,
            incident_pairs_active=set(),
            tp_completed={1: False, 2: False},
            stop_filled={1: False, 2: False},
            tp_has_fill=lambda _idx: False,
            stop_has_fill=lambda _idx: False,
        )
        is None
    )

    assert (
        select_detached_incident_pair(
            order_id_value=101,
            code=201,
            leg_by_order_id=leg_map,
            incident_pairs_active=set(),
            tp_completed={1: False, 2: False},
            stop_filled={1: False, 2: False},
            tp_has_fill=lambda _idx: False,
            stop_has_fill=lambda _idx: False,
        )
        == 1
    )

    assert (
        select_detached_incident_pair(
            order_id_value=101,
            code=202,
            leg_by_order_id=leg_map,
            incident_pairs_active=set(),
            tp_completed={1: True, 2: False},
            stop_filled={1: False, 2: False},
            tp_has_fill=lambda _idx: False,
            stop_has_fill=lambda _idx: False,
        )
        is None
    )

    assert (
        select_detached_incident_pair(
            order_id_value=102,
            code=202,
            leg_by_order_id=leg_map,
            incident_pairs_active=set(),
            tp_completed={1: False, 2: False},
            stop_filled={1: False, 2: True},
            tp_has_fill=lambda _idx: False,
            stop_has_fill=lambda _idx: False,
        )
        is None
    )

    assert (
        select_detached_incident_pair(
            order_id_value=102,
            code=202,
            leg_by_order_id=leg_map,
            incident_pairs_active=set(),
            tp_completed={1: False, 2: False},
            stop_filled={1: False, 2: False},
            tp_has_fill=lambda _idx: False,
            stop_has_fill=lambda _idx: False,
        )
        == 2
    )

    assert (
        select_detached_incident_pair(
            order_id_value=102,
            code=404,
            leg_by_order_id=leg_map,
            incident_pairs_active={2},
            tp_completed={1: False, 2: False},
            stop_filled={1: False, 2: False},
            tp_has_fill=lambda _idx: False,
            stop_has_fill=lambda _idx: False,
        )
        is None
    )
