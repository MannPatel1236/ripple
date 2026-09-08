# Ripple — Travel Disruption Recovery Engine

**HackCelestial 3.0 · PS 2**

> Every disruption tool tells you the flight is late. Ripple tells you what
> that costs across the rest of your trip — and hands you three priced ways out.

---

## Run it

```bash
pip install -r requirements.txt
python3 -m uvicorn backend.api:app --reload --port 8000
```

Open **http://localhost:8000**

No database, no accounts, no API keys needed to run the demo. Policies are
pre-parsed and cached in `data/policies.json`.

```bash
python3 tests/test_engines.py     # 29 tests, no pytest needed
```

---

## The 90-second demo

1. **Trip loads** — 6 bookings, 5 vendors, ₹42,500 total, all green.
   Dependencies visible as buffer times between bookings.
2. **Click "Delay flight 5h."**
3. **The cascade** — transfer goes red (departs before you land), dinner show
   goes red, hotel goes amber, return flight stays green. Every red box says
   *why* in plain language.
4. **Financial exposure** — ₹19,600 at risk, ₹7,000 already lost, ₹15,900 safe.
5. **The countdown** — ⏱ *60 minutes before the refund window closes on the
   airport transfer.* Live ticking. This is the moment the demo lands.
6. **Three plans**, each with real numbers:

   | Plan | Money lost | Trip kept |
   |---|---|---|
   | Cut losses | ₹7,000 | 66.6% |
   | Protect money | ₹12,600 | 80.7% |
   | Preserve experience | ₹13,300 | 100% |

7. **Click any booking** → the policy modal shows the vendor's actual sentence
   on the left and the extracted numbers on the right. Every rupee is traceable.
8. **Pick a plan** → read-only action checklist with per-item deadlines.

---

## Architecture

```
Trip ─► Chain Builder ─► Policy Parser ─► Impact Engine
                                        ─► Recovery Generator ─► Cost & Ranking
```

| Module | File | Responsibility |
|---|---|---|
| Models | `backend/models.py` | The schema contract. Written first. |
| Policy engine | `backend/policy_engine.py` | Refund arithmetic, cliff detection |
| Impact engine | `backend/impact_engine.py` | Chain propagation, feasibility |
| Recovery engine | `backend/recovery_engine.py` | One scoring formula, three presets |
| API | `backend/api.py` | FastAPI, stateless |
| Frontend | `frontend/index.html` | Single file, no build step |
| Parser | `scripts/parse_policies.py` | Offline LLM extraction → cache |

### Two design decisions that matter

**Bookings do not move when you are late.** The hotel doesn't reschedule
itself; the tour still departs at 06:00. A disruption doesn't shift the
itinerary — it makes parts of it infeasible while everything stays put. So
propagation is *feasibility evaluation*, not rescheduling. Simpler to
implement, and it's what actually happens.

**One scoring function, three presets — not three strategies.**

```
score = w_money·MoneySaved + w_time·TimeSaved
      + w_experience·TripPreserved − w_risk·Risk
```

The three plans are three weight vectors over that formula. When asked "why
exactly three?", the honest answer is *"three preference profiles over one
optimisation engine; the weights are what change."* That answer only holds
because the code is genuinely built this way.

### Where the LLM sits

Extraction is probabilistic. Decision-making is not.

```
Policy text → LLM → JSON → Pydantic validation → deterministic engine
```

A malformed parse is rejected by the schema, never silently trusted. Every
figure shown carries the `source_clause` it came from.

---

## Scope — stated honestly

Alternatives come from a **frozen dataset** for one corridor (Mumbai–Delhi).
The policy shapes are real (slab-based airline fees, hotel first-night
retention, non-refundable tours). Live inventory is a commercial API
integration, not a research problem — **the reasoning layer is the contribution.**

**Not built, deliberately:** live inventory APIs, booking execution, accounts,
database, email parsing, group trips. The action checklist is guidance only;
Ripple never cancels anything on the traveller's behalf.

---

## Using real policies

```bash
export ANTHROPIC_API_KEY=sk-...
python3 scripts/parse_policies.py data/raw_policies/
```

Collect 15–20 policies chosen for **shape variety**, not volume: slab-based
(IRCTC, airline fee tables), binary non-refundable, single cutoff,
percentage-based, fee-plus-partial, and at least one genuinely messy prose
paragraph. That last one is the proof asset — a parser that handles a clean
table is unremarkable; one that extracts a penalty curve from an ugly
paragraph is what makes a judge sit up.

Run with no arguments to print the extraction prompt without spending anything.
