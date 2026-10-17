#!/usr/bin/env bash
# Run all four services plus the web app, then reset and seed (store and signer from .env).
# DEMO_SCENE=waiting|sara starts a demo scene instead of a clean config (docs/DEMO.md).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "created .env from .env.example (local defaults)"
fi

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

run() {
  local module=$1 port=$2
  uv run uvicorn "$module:app" --port "$port" --no-access-log --reload --reload-dir packages --reload-dir services --reload-dir agents &
  pids+=($!)
}

run trust_gateway.app 8000
run vendor_svc.app 8001
run retailer_svc.app 8002
run mocks.app 8009

if [[ -f web/package.json ]]; then
  (cd web && npm run dev) &
  pids+=($!)
fi

# Reset only once every service answers, so nothing started after "ready" is wiped by it.
for port in 8000 8001 8002 8009; do
  until curl -sf -o /dev/null "http://localhost:${port}/healthz"; do sleep 0.3; done
done
if uv run python scripts/demo_reset.py ${DEMO_SCENE:+--scene "$DEMO_SCENE"}; then
  echo "VendorLink ready: vendor app http://localhost:5173/vendor · retailer console http://localhost:5173/retailer"
else
  echo "demo_reset failed; run it by hand"
fi

wait
