"""A vendor uploads a 'licence' PDF with hidden instructions. It must be BLOCKED before any LLM
sees it, the vendor quarantined, and the event visible.

    uv run python scripts/attack_injected_doc.py [--live]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

from scripts._demo import DemoError, call, reset
from vl_core.settings import Settings, get_settings

PDF = Path(__file__).resolve().parents[1] / "seed" / "attack" / "injected.pdf"
SAM = "vendor:sam"


async def run(s: Settings) -> None:
    await reset(s, "A")
    print("1. Sam uploads an FSSAI 'licence' PDF containing hidden instructions")
    doc = await call(
        s.vendor_svc_url,
        "POST",
        f"/vendors/{SAM}/documents",
        json={
            "doc_type": "fssai",
            "number": "10019022000999",
            "content_type": "application/pdf",
            "filename": "fssai-licence.pdf",
            "data_b64": base64.b64encode(PDF.read_bytes()).decode(),
        },
    )
    print(f"  document status: {doc['status']} ({doc['failure_reason']})")
    if doc["status"] != "failed" or doc["failure_reason"] != "BLOCKED_BY_SCREENING":
        raise DemoError("injected document was accepted")

    events = await call(s.trust_gateway_url, "GET", "/security-events")
    injection = [e for e in events if e["type"] == "injection"]
    if not injection:
        raise DemoError("no injection security event")
    print(f"  security event: {injection[-1]['evidence'][:120]}…")
    print(f"  action taken: {injection[-1]['action_taken']}")

    print("2. The quarantined vendor's agent tries to continue onboarding")
    r = await call(s.vendor_svc_url, "POST", f"/vendors/{SAM}/apply", expect=None)
    code = r.get("detail", {}).get("error")
    print(f"  apply → {code}")
    if code != "quarantined":
        raise DemoError("quarantined vendor was not stopped")
    print("INJECTED DOCUMENT BLOCKED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        settings = get_settings()
    else:
        from scripts._stack import build_stack

        settings = build_stack()
    try:
        asyncio.run(run(settings))
    except DemoError as exc:
        print(f"ATTACK NOT BLOCKED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
