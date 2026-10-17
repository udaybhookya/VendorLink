"""Order loop, text only: green, red → counter → amber → "yes" → signed, and amber → "no".

uv run python scripts/e2e_order_loop.py              # in-process (CI, no ports)
uv run python scripts/e2e_order_loop.py --live       # against `make dev`
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from scripts._demo import (
    RETAILER,
    DemoError,
    call,
    day,
    deal,
    place,
    reset,
    retailer_order,
    say_yes,
)
from vl_core.settings import Settings, get_settings


def check(cond: bool, message: str) -> None:
    print(f"  {'✓' if cond else '✗'} {message}")
    if not cond:
        raise DemoError(message)


async def run(s: Settings) -> None:
    await reset(s, "A")

    print("GREEN · Hub 7 orders 100 bunches @ 07:00 (inside Maya's rules, under auto-accept)")
    o = await place(s, "store:hub-7", day(1))
    check(
        o["status"] == "confirmed",
        f"retailer order confirmed without asking Maya ({o['status']})",
    )
    check(o["decisions"][-1]["status"] == "green", "Guardian decision is green")

    print("RED · Hub 4 orders 350 bunches @ 05:30 (demo anchor)")
    o = await place(s, "store:hub-4", day(2))
    first = o["decisions"][0]
    check(first["status"] == "red", "Guardian returns red")
    codes = {r["code"] for r in first["reasons"]}
    check(codes == {"QTY_OVER_DAILY_MAX", "PICKUP_BEFORE_WINDOW"}, f"reasons {sorted(codes)}")
    counter = o["versions"][-1]
    check(
        counter["version"] == 2 and counter["lines"][0]["qty"] == "300",
        "counter is v2 with 300 bunches",
    )
    check(
        "T00:30:00Z" in counter["pickup_at"] or "06:00" in counter["pickup_at"],
        f"counter pickup 06:00 IST ({counter['pickup_at']})",
    )
    check(o["status"] == "awaiting_vendor", "retailer auto-accepted the counter; waits for Maya")
    view = await deal(s, o["order_id"])
    check(view["deal"]["status"] == "awaiting_consent", "Maya's deal is amber, needs her yes")
    print(f"    read-back: {view['readback']}")
    signed = await say_yes(s, o["order_id"])
    check(signed["deal"]["status"] == "signed", "Maya said 'అవును' → Guardian signed v2")
    o = await retailer_order(s, o["order_id"])
    check(o["status"] == "confirmed", "retailer signed → Order Mandate filed at the gateway")
    mandates = await call(s.trust_gateway_url, "GET", "/mandates/order")
    m = next(m for m in mandates if m["order_id"] == o["order_id"])
    check(m["final_version"] == 2 and m["consent_ref"], "mandate is for v2 and cites her consent")

    print("AMBER · Hub 9 orders 280 bunches @ 06:30 (inside rules, over auto-accept)")
    o = await place(s, "store:hub-9", day(3))
    check(o["decisions"][-1]["status"] == "amber", "Guardian returns amber")
    check(o["status"] == "awaiting_vendor", "retailer waits for Maya")
    refused = await call(
        s.vendor_svc_url,
        "POST",
        f"/vendors/vendor:maya/deals/{o['order_id']}/consent",
        expect=422,
        json={
            "subject_hash": (await deal(s, o["order_id"]))["subject_hash"],
            "transcript": "వద్దు",
            "language": "te",
        },
    )
    check(refused["detail"]["error"] == "not_a_yes", "'వద్దు' (no) is not consent; nothing signed")

    verify = await call(s.trust_gateway_url, "GET", "/audit/verify")
    check(verify["ok"], f"audit chain verifies ({verify['checked']} records)")
    active = await call(s.retailer_svc_url, "GET", f"/retailers/{RETAILER}/config/active")
    check(
        active["config_hash"]
        in {r["config_hash"] for r in await call(s.trust_gateway_url, "GET", "/audit")},
        "audit records carry the active config hash",
    )
    print("ORDER LOOP PASSED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use running services")
    args = parser.parse_args()
    if args.live:
        settings = get_settings()
    else:
        from scripts._stack import build_stack

        settings = build_stack()
    try:
        asyncio.run(run(settings))
    except DemoError as exc:
        print(f"ORDER LOOP FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
