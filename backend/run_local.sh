#!/usr/bin/env bash
# Start the Kavach backend locally (macOS/Linux). Reads backend/.env if present.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export KAVACH_MODE="${KAVACH_MODE:-local}" KAVACH_TTS="${KAVACH_TTS:-auto}"
[ -f assets/demo_computer_networks.pdf ] || python scripts/make_demo_pdf.py
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
