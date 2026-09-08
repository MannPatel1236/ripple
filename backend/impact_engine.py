"""
Impact engine.

CORE DESIGN INSIGHT — do not "fix" this into a rescheduler:

    Bookings do NOT move when you are late. The hotel does not reschedule
    itself. The tour still departs at 06:00. A disruption does not shift the
    itinerary; it makes parts of it INFEASIBLE or WASTED while everything
    stays exactly where it was.

So propagation is feasibility evaluation, not time-shifting. We walk the chain
in topological order and ask each node: given upstream reality, can I still
make this?
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta

from .models import Disruption, Edge, Exposure, Node, Trip
from .policy_engine import PolicyStore, next_refund_deadline


def topological_order(trip: Trip) -> list[str]:
    indegree = {n.id: 0 for n in trip.nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in trip.nodes}
    for e in trip.edges:
        adjacency[e.src].append(e.dst)
        indegree[e.dst] += 1

    queue = [nid for nid, d in indegree.items() if d == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for nxt in adjacency[nid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    # Any node in a cycle (shouldn't happen) still gets evaluated.
    for n in trip.nodes:
        if n.id not in order:
            order.append(n.id)
    return order


def incoming_edges(trip: Trip, node_id: str) -> list[Edge]:
    return [e for e in trip.edges if e.dst == node_id]


def _effective_end(node: Node) -> datetime:
    return node.end + timedelta(minutes=node.delay_minutes)


def _effective_start(node: Node) -> datetime:
    return node.start + timedelta(minutes=node.delay_minutes)


def propagate(trip: Trip, disruption: Disruption) -> Trip:
    """Apply a disruption and evaluate the whole chain. Returns a new Trip."""
    t = copy.deepcopy(trip)

    # Reset
    for n in t.nodes:
        n.status = "OK"
        n.reason = None
        n.delay_minutes = 0

    # Apply the disruption to exactly one node.
    origin = t.node(disruption.node_id)
    if disruption.cancelled:
        origin.status = "BROKEN"
        origin.reason = "Cancelled by the operator"
    elif disruption.delay_minutes:
        origin.delay_minutes = disruption.delay_minutes
        origin.status = "AT_RISK"
        hrs = disruption.delay_minutes / 60
        origin.reason = (
            f"Delayed {hrs:.0f}h — now arriving "
            f"{_effective_end(origin).strftime('%H:%M on %d %b')}"
        )

    # Walk downstream. Bookings stay put; we test whether they survive.
    for node_id in topological_order(t):
        node = t.node(node_id)
        for edge in incoming_edges(t, node_id):
            src = t.node(edge.src)

            # A dead upstream node poisons anything that hard-depends on it.
            if src.status == "BROKEN" and edge.hard:
                if node.status != "BROKEN":
                    node.status = "AT_RISK"
                    node.reason = f"Depends on {src.label}, which is broken"
                continue

            src_end = (
                _effective_start(src)
                if edge.measure_from == "start"
                else _effective_end(src)
            )
            dst_start = _effective_start(node)

            if edge.kind in ("MIN_GAP", "ARRIVE_BEFORE"):
                gap_minutes = (dst_start - src_end).total_seconds() / 60.0
                required = edge.slack_minutes

                if gap_minutes < 0:
                    # The dependency is already in the past.
                    if edge.hard:
                        node.status = "BROKEN"
                        node.reason = (
                            f"{src.label} now ends after this starts — "
                            f"missed by {abs(gap_minutes):.0f} min"
                        )
                    elif node.status == "OK":
                        node.status = "AT_RISK"
                        node.reason = f"Overlaps with {src.label}"
                elif gap_minutes < required:
                    if edge.hard:
                        node.status = "BROKEN"
                        node.reason = (
                            f"Only {gap_minutes:.0f} min after {src.label}; "
                            f"{required} min needed"
                        )
                    elif node.status == "OK":
                        node.status = "AT_RISK"
                        node.reason = (
                            f"Tight: {gap_minutes:.0f} min after {src.label}, "
                            f"{required} min recommended"
                        )
    return t


def compute_exposure(
    trip: Trip, store: PolicyStore, now: datetime
) -> Exposure:
    """
    Financial exposure: how much of the trip is safe, at risk, already lost.

    Pure arithmetic over node status and policy refund tiers.
    """
    safe = at_risk = lost = 0.0
    affected = 0
    deadlines: list[tuple[datetime, str]] = []

    for node in trip.nodes:
        policy = store.get(node.policy_id)
        if node.status == "OK":
            safe += node.cost
        elif node.status == "AT_RISK":
            at_risk += node.cost
            affected += 1
        else:  # BROKEN
            unrecoverable = node.cost - _best_case_refund(policy, now, node)
            lost += unrecoverable
            at_risk += node.cost - unrecoverable
            affected += 1

        if node.status in ("AT_RISK", "BROKEN"):
            deadline, _cur, _after = next_refund_deadline(policy, now, node.start)
            if deadline and deadline > now:
                deadlines.append((deadline, node.label))

    deadlines.sort(key=lambda x: x[0])
    horizon = now + timedelta(hours=6)
    approaching = sum(1 for d, _ in deadlines if d <= horizon)

    return Exposure(
        total_trip_value=round(trip.total_value, 2),
        safe=round(safe, 2),
        at_risk=round(at_risk, 2),
        lost=round(lost, 2),
        affected_count=affected,
        deadlines_approaching=approaching or len(deadlines),
        next_deadline=deadlines[0][0] if deadlines else None,
        next_deadline_label=deadlines[0][1] if deadlines else None,
    )


def _best_case_refund(policy, now: datetime, node: Node) -> float:
    from .policy_engine import refund_value
    return refund_value(policy, now, node.start)
