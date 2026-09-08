"""
Tests for the deterministic core.

These cover the things that are easy to get subtly wrong and that would
embarrass you on stage — refund tier boundaries, multi-day bookings, and
the rule that the delayed booking itself is never cancelled.

Run:  python3 tests/test_engines.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.models import Disruption, Policy, RefundRule, Trip  # noqa: E402
from backend.impact_engine import compute_exposure, propagate  # noqa: E402
from backend.policy_engine import (  # noqa: E402
    PolicyStore,
    next_refund_deadline,
    refund_value,
)
from backend.recovery_engine import generate_options  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")


def load():
    trip = Trip(**json.loads((ROOT / "data" / "trip.json").read_text()))
    return trip, PolicyStore()


# ---------------------------------------------------------------- policy
print("\npolicy engine")

p = Policy(
    policy_id="t",
    booking_label="test",
    total=1000,
    refund_rules=[
        RefundRule(before_hours=48, refund_pct=100),
        RefundRule(before_hours=4, refund_pct=50),
        RefundRule(before_hours=0, refund_pct=0),
    ],
    source_clause="test clause",
)
start = datetime(2026, 1, 10, 12, 0)

check("full refund outside 48h", refund_value(p, start - timedelta(hours=72), start) == 1000)
check("boundary at exactly 48h is inclusive",
      refund_value(p, start - timedelta(hours=48), start) == 1000)
check("mid tier between 48h and 4h",
      refund_value(p, start - timedelta(hours=24), start) == 500)
check("no refund inside 4h", refund_value(p, start - timedelta(hours=1), start) == 0)
check("no refund after departure", refund_value(p, start + timedelta(hours=1), start) == 0)

dl, cur, aft = next_refund_deadline(p, start - timedelta(hours=72), start)
check("cliff is when the CURRENT tier expires", dl == start - timedelta(hours=48),
      f"got {dl}")
check("value drops across the cliff", cur == 1000 and aft == 500, f"{cur}->{aft}")

dl2, c2, a2 = next_refund_deadline(p, start - timedelta(hours=1), start)
check("no cliff once in the worst tier", dl2 is None and c2 == a2 == 0)

# ---------------------------------------------------------------- impact
print("\nimpact engine")

trip, store = load()
now = datetime(2026, 11, 20, 17, 15)
clean = propagate(trip, Disruption(node_id="n1", delay_minutes=0))
check("undisrupted trip is fully OK",
      all(n.status == "OK" for n in clean.nodes),
      [f"{n.id}:{n.status}" for n in clean.nodes if n.status != "OK"])

res = propagate(trip, Disruption(node_id="n1", delay_minutes=300))
check("delayed flight is flagged at risk", res.node("n1").status == "AT_RISK")
check("transfer breaks (departs before arrival)", res.node("n2").status == "BROKEN")
check("dinner show breaks", res.node("n4").status == "BROKEN")
check("return flight stays safe", res.node("n6").status == "OK")
check("multi-day hotel measured from check-in, not check-out",
      res.node("n5").status != "BROKEN",
      "tour wrongly broken by hotel checkout date")
check("every broken node explains itself",
      all(n.reason for n in res.nodes if n.status != "OK"))

exp = compute_exposure(res, store, now)
check("exposure sums to trip value",
      abs((exp.safe + exp.at_risk + exp.lost) - exp.total_trip_value) < 1,
      f"{exp.safe}+{exp.at_risk}+{exp.lost} != {exp.total_trip_value}")
check("a refund deadline is surfaced", exp.next_deadline is not None)
check("deadline lies in the future", exp.next_deadline > now)

# ---------------------------------------------------------------- recovery
print("\nrecovery engine")

dis = Disruption(node_id="n1", delay_minutes=300)
opts = generate_options(res, store, now, dis)

check("all three presets produce a plan", len(opts) == 3, f"got {len(opts)}")
check("all plans pass their own feasibility replay",
      all(o.feasible for o in opts))
check("the delayed flight is never cancelled",
      all(a.kind != "CANCEL"
          for o in opts for a in o.actions if a.node_id == "n1"),
      "engine tried to cancel the flight the traveller is on")
check("preserve-experience keeps more than cut-losses",
      next(o for o in opts if o.strategy == "PRESERVE_EXPERIENCE").preserved_pct
      > next(o for o in opts if o.strategy == "CUT_LOSSES").preserved_pct)
check("cut-losses loses less money than preserve-experience",
      next(o for o in opts if o.strategy == "CUT_LOSSES").money_lost
      < next(o for o in opts if o.strategy == "PRESERVE_EXPERIENCE").money_lost)
check("there is a genuine tradeoff (no plan dominates)",
      len({(o.money_lost, o.preserved_pct) for o in opts}) == 3)
check("every action carries an explanation",
      all(a.explanation for o in opts for a in o.actions))
check("weights differ across presets",
      len({tuple(sorted(o.weights.items())) for o in opts}) == 3)

# ---------------------------------------------------------------- data
print("\nfrozen dataset")

check("every booking references a real policy",
      all(n.policy_id in store.policies for n in trip.nodes))
check("every policy carries a source clause",
      all(p.source_clause.strip() for p in store.policies.values()))
check("edges reference real nodes",
      all(any(n.id == e.src for n in trip.nodes)
          and any(n.id == e.dst for n in trip.nodes) for e in trip.edges))

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
