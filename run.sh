#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
pip install -r requirements.txt -q
echo "Ripple running at http://localhost:8000"
python3 -m uvicorn backend.api:app --reload --port 8000
