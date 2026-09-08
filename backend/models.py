"""
Ripple — data models.

This file is the CONTRACT. Every module codes against these types.
Committed in hour 1 of the build; nothing else starts until it exists.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NodeType = Literal["FLIGHT", "TRAIN", "TRANSFER", "HOTEL", "TOUR", "EVENT"]
NodeStatus = Literal["OK", "AT_RISK", "BROKEN"]
EdgeKind = Literal["MIN_GAP", "ARRIVE_BEFORE", "SAME_LOCATION"]
ActionKind = Literal["KEEP", "CANCEL", "REBOOK", "SHIFT"]
StrategyName = Literal["PRESERVE_EXPERIENCE", "PROTECT_MONEY", "CUT_LOSSES"]


# --------------------------------------------------------------------------
# Trip structure
# --------------------------------------------------------------------------
class Node(BaseModel):
    """One booking in the trip."""
    id: str
    type: NodeType
    label: str
    vendor: str
    start: datetime
    end: datetime
    from_loc: str | None = None
    to_loc: str | None = None
    cost: float
    policy_id: str
    status: NodeStatus = "OK"
    # Populated by the impact engine so the UI can explain itself.
    reason: str | None = None
    delay_minutes: int = 0


class Edge(BaseModel):
    """A dependency between two bookings."""
    src: str
    dst: str
    kind: EdgeKind
    slack_minutes: int = 0        # required buffer between src and dst.start
    hard: bool = True             # hard = violation makes dst infeasible
    # Multi-day bookings (a 3-night hotel) must be measured from check-IN,
    # not check-out, or every later booking looks impossible.
    measure_from: Literal["end", "start"] = "end"
    note: str | None = None


class Alternative(BaseModel):
    """A replacement option for a broken booking (frozen dataset)."""
    id: str
    replaces_type: NodeType
    label: str
    vendor: str
    start: datetime
    end: datetime
    cost: float
    note: str | None = None


class Trip(BaseModel):
    id: str
    title: str
    nodes: list[Node]
    edges: list[Edge]

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    @property
    def total_value(self) -> float:
        return sum(n.cost for n in self.nodes)


# --------------------------------------------------------------------------
# Policy — the differentiator
# --------------------------------------------------------------------------
class RefundRule(BaseModel):
    """
    'If cancelled at least `before_hours` before the booking starts,
     you get `refund_pct` of the total back.'
    Rules are evaluated most-generous-window-first.
    """
    before_hours: float
    refund_pct: float


class Policy(BaseModel):
    policy_id: str
    booking_label: str
    total: float
    refund_rules: list[RefundRule]
    change_allowed: bool = False
    change_fee: float = 0.0
    # NOT optional. Every rupee we show must be traceable to a sentence.
    source_clause: str
    source_url: str | None = None
    raw_text: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------
class Action(BaseModel):
    node_id: str
    node_label: str
    kind: ActionKind
    alternative_id: str | None = None
    new_time: datetime | None = None
    # Financial consequence of this single action.
    refund_expected: float = 0.0
    extra_cost: float = 0.0
    money_lost: float = 0.0
    deadline: datetime | None = None      # act-before time, drives the countdown
    explanation: str = ""


class RecoveryOption(BaseModel):
    strategy: StrategyName
    title: str
    subtitle: str
    actions: list[Action]
    money_lost: float
    time_lost_minutes: int
    preserved_pct: float
    score: float
    feasible: bool
    weights: dict[str, float]


# --------------------------------------------------------------------------
# Financial exposure — the demo's emotional peak
# --------------------------------------------------------------------------
class Exposure(BaseModel):
    total_trip_value: float
    safe: float
    at_risk: float
    lost: float
    affected_count: int
    deadlines_approaching: int
    next_deadline: datetime | None = None
    next_deadline_label: str | None = None


class Disruption(BaseModel):
    node_id: str
    delay_minutes: int = 0
    cancelled: bool = False


class ImpactResult(BaseModel):
    trip: Trip
    exposure: Exposure
    options: list[RecoveryOption]
