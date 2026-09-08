"""
Policy engine.

The LLM's only job is turning prose into a Policy object (see scripts/parse_policies.py).
Everything in THIS file is deterministic arithmetic. That separation is the
architecture: extraction is probabilistic, decision-making is not.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .models import Alternative, Policy

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class PolicyStore:
    def __init__(self, path: Path | None = None) -> None:
        path = path or (DATA_DIR / "policies.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.policies: dict[str, Policy] = {
            p["policy_id"]: Policy(**p) for p in raw["policies"]
        }
        self.alternatives: list[Alternative] = [
            Alternative(**a) for a in raw.get("alternatives", [])
        ]

    def get(self, policy_id: str) -> Policy:
        return self.policies[policy_id]

    def alternatives_for(self, node_type: str) -> list[Alternative]:
        return [a for a in self.alternatives if a.replaces_type == node_type]


def refund_pct_at(policy: Policy, cancel_at: datetime, booking_start: datetime) -> float:
    """
    Which refund tier applies if we cancel at `cancel_at`?

    Rules are windows expressed as 'at least N hours before the booking'.
    We take the most generous window the cancellation time still qualifies for.
    """
    hours_before = (booking_start - cancel_at).total_seconds() / 3600.0
    best = 0.0
    for rule in sorted(policy.refund_rules, key=lambda r: r.before_hours, reverse=True):
        if hours_before >= rule.before_hours:
            best = rule.refund_pct
            break
    else:
        # Past every window (including a 0-hour rule): last rule governs.
        if policy.refund_rules:
            best = min(policy.refund_rules, key=lambda r: r.before_hours).refund_pct
    return best


def refund_value(policy: Policy, cancel_at: datetime, booking_start: datetime) -> float:
    """Rupees back if cancelled at `cancel_at`. The core function of the system."""
    pct = refund_pct_at(policy, cancel_at, booking_start)
    return round(policy.total * pct / 100.0, 2)


def money_lost_if_cancelled(
    policy: Policy, cancel_at: datetime, booking_start: datetime
) -> float:
    return round(policy.total - refund_value(policy, cancel_at, booking_start), 2)


def next_refund_deadline(
    policy: Policy, now: datetime, booking_start: datetime
) -> tuple[datetime | None, float, float]:
    """
    The moment the refund drops to a worse tier.

    Returns (deadline, current_refund, refund_after_deadline).
    This is what drives the countdown in the UI — the single most
    persuasive element of the demo, and it is pure arithmetic over
    fields we already store.
    """
    if not policy.refund_rules:
        return None, 0.0, 0.0

    # Most generous window first.
    rules = sorted(policy.refund_rules, key=lambda r: r.before_hours, reverse=True)
    hours_before = (booking_start - now).total_seconds() / 3600.0

    # Which rule applies right now?
    current_idx = len(rules) - 1
    for i, rule in enumerate(rules):
        if hours_before >= rule.before_hours:
            current_idx = i
            break

    current_pct = rules[current_idx].refund_pct
    current_value = round(policy.total * current_pct / 100.0, 2)

    # Already in the last (worst) tier — nothing further to lose by waiting.
    if current_idx >= len(rules) - 1:
        return None, current_value, current_value

    # The cliff is when we stop qualifying for the CURRENT tier, not when the
    # next one nominally starts. Those are the same instant, and getting it
    # wrong makes the countdown point at the wrong moment.
    deadline = booking_start - timedelta(hours=rules[current_idx].before_hours)
    next_pct = rules[current_idx + 1].refund_pct

    if deadline <= now:
        return None, current_value, current_value

    return (
        deadline,
        current_value,
        round(policy.total * next_pct / 100.0, 2),
    )
