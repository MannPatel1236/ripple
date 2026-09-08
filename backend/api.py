"""Ripple API. Stateless: the trip is loaded from disk on every request."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .impact_engine import compute_exposure, propagate
from .models import Disruption, Trip
from .policy_engine import PolicyStore, next_refund_deadline, refund_value
from .recovery_engine import generate_options

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="Ripple — Travel Disruption Recovery Engine", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = PolicyStore()


def load_trip() -> Trip:
    return Trip(**json.loads((DATA_DIR / "trip.json").read_text(encoding="utf-8")))


def demo_now(trip: Trip) -> datetime:
    """
    'Now' for the demo: shortly before the first booking.

    The countdown is the most persuasive element on stage, so the clock is
    anchored to the dataset rather than to the wall clock — otherwise the
    demo silently stops working the day after the data was written.
    """
    first = min(n.start for n in trip.nodes)
    return first - timedelta(minutes=15)


@app.get("/api/trip")
def get_trip():
    trip = load_trip()
    now = demo_now(trip)
    return {
        "trip": trip.model_dump(mode="json"),
        "now": now.isoformat(),
        "total_value": trip.total_value,
        "exposure": compute_exposure(trip, store, now).model_dump(mode="json"),
    }


@app.post("/api/disrupt")
def disrupt(disruption: Disruption):
    trip = load_trip()
    now = demo_now(trip)
    try:
        trip.node(disruption.node_id)
    except KeyError:
        raise HTTPException(404, f"No booking with id {disruption.node_id}")

    impacted = propagate(trip, disruption)
    exposure = compute_exposure(impacted, store, now)
    options = generate_options(impacted, store, now, disruption)

    return {
        "trip": impacted.model_dump(mode="json"),
        "now": now.isoformat(),
        "exposure": exposure.model_dump(mode="json"),
        "options": [o.model_dump(mode="json") for o in options],
    }


@app.get("/api/policy/{policy_id}")
def get_policy(policy_id: str):
    """Every rupee on screen must be traceable to a sentence. This is that link."""
    try:
        policy = store.get(policy_id)
    except KeyError:
        raise HTTPException(404, f"No policy {policy_id}")

    trip = load_trip()
    now = demo_now(trip)
    node = next((n for n in trip.nodes if n.policy_id == policy_id), None)
    payload = policy.model_dump(mode="json")

    if node:
        deadline, current, after = next_refund_deadline(policy, now, node.start)
        payload["refund_now"] = refund_value(policy, now, node.start)
        payload["next_deadline"] = deadline.isoformat() if deadline else None
        payload["refund_before_deadline"] = current
        payload["refund_after_deadline"] = after
    return payload


@app.get("/api/health")
def health():
    return {"ok": True, "policies": len(store.policies)}


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")
