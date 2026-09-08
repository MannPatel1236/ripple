"""
Offline policy parser — run this BEFORE the demo, never during it.

Takes raw cancellation-policy text and produces the structured Policy objects
in data/policies.json. Results are cached by a hash of the input text, so the
same policy is never billed twice and the demo never depends on a network call.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python3 scripts/parse_policies.py data/raw_policies/

If no API key is set, this prints the prompt and exits — useful for showing
judges exactly what the extraction step does without spending anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "parse_cache"

SCHEMA = {
    "policy_id": "string, slug of the booking",
    "booking_label": "string, human name of the booking",
    "total": "number, total amount paid in INR",
    "refund_rules": [
        {
            "before_hours": "number — cancel at least this many hours before the "
                            "booking starts to qualify",
            "refund_pct": "number 0-100 — percentage of total refunded in that window",
        }
    ],
    "change_allowed": "boolean",
    "change_fee": "number, INR",
    "source_clause": "string — the EXACT sentence(s) from the input that these "
                     "rules came from. Never paraphrase. Never invent.",
    "confidence": "number 0-1 — how certain the extraction is",
}

PROMPT = """You convert travel cancellation policies into structured data.

Return ONLY valid JSON matching this schema. No markdown, no commentary:

{schema}

Rules:
- refund_rules must be sorted from the most generous window to the least.
- Always include a 0-hour rule describing the no-show / after-departure case.
- source_clause must be copied verbatim from the input. If you cannot find a
  sentence that supports a rule, lower the confidence instead of inventing one.
- If the policy is ambiguous, set confidence below 0.7 and extract the most
  conservative (least favourable to the traveller) reading.

Booking label: {label}
Total paid: INR {total}

Policy text:
\"\"\"
{text}
\"\"\"
"""


def cache_key(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def parse_one(text: str, label: str, total: float) -> dict | None:
    """Extract one policy. Cached by content hash."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = cache_key(text)
    cached = CACHE / f"{key}.json"
    if cached.exists():
        print(f"  cache hit  {label}")
        return json.loads(cached.read_text())

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    prompt = PROMPT.format(
        schema=json.dumps(SCHEMA, indent=2), label=label, total=total, text=text
    )
    if not api_key:
        print("\n--- no ANTHROPIC_API_KEY set; prompt that WOULD be sent ---\n")
        print(prompt)
        return None

    try:
        import anthropic
    except ImportError:
        print("pip install anthropic --break-system-packages")
        return None

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  PARSE FAILED for {label} — model returned non-JSON")
        return None

    # Validate against the real schema before trusting it. This is the point:
    # extraction is probabilistic, everything downstream is not.
    sys.path.insert(0, str(ROOT))
    from backend.models import Policy

    try:
        Policy(**data)
    except Exception as exc:
        print(f"  SCHEMA REJECTED for {label}: {exc}")
        return None

    cached.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  parsed     {label}  (confidence {data.get('confidence')})")
    return data


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        # Demo the prompt on the hotel clause already in the dataset.
        policies = json.loads((ROOT / "data" / "policies.json").read_text())
        hotel = next(p for p in policies["policies"] if p["policy_id"] == "p_hotel")
        parse_one(hotel["source_clause"], hotel["booking_label"], hotel["total"])
        return

    src = Path(sys.argv[1])
    files = sorted(src.glob("*.txt"))
    print(f"parsing {len(files)} policy files from {src}")
    out = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        result = parse_one(text, f.stem, 0)
        if result:
            out.append(result)
    print(f"\n{len(out)} policies parsed into {CACHE}")


if __name__ == "__main__":
    main()
