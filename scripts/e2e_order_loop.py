"""Order loop, text only: red → counter → buyer approves on Slack → "yes" → signed; then, on
standard terms, green, and amber → "no".

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
    outbox,
    place,
    reply,
    reset,
    retailer_order,
    say_yes,
    tick,
    use_terms,
)
from vl_core.settings import Settings, get_settings


def check(cond: bool, message: str) -> None:
    print(f"  {'✓' if cond else '✗'} {message}")
    if not cond:
        raise DemoError(message)


async def run(s: Settings) -> None:
    await reset(s, "A")

    print("RED · the corporate gifting desk orders 50 candles @ 08:00, paid in 45 days (anchor)")
    o = await place(s, "store:hub-4", day(2))
    first = o["decisions"][0]
    check(first["status"] == "red", "Guardian returns red")
    codes = {r["code"] for r in first["reasons"]}
    expected = {"QTY_OVER_DAILY_MAX", "PICKUP_BEFORE_WINDOW", "PAYMENT_TOO_LATE"}
    check(codes == expected, f"reasons {sorted(codes)}")
    counter = o["versions"][-1]
    line = counter["lines"][0]
    check(
        counter["version"] == 2 and line["qty"] == "40" and line["price"] == "220",
        "counter is v2: 40 candles, price unchanged at ₹220",
    )
    check(
        "T04:30:00Z" in counter["pickup_at"] or "10:00" in counter["pickup_at"],
        f"counter pickup 10:00 IST ({counter['pickup_at']})",
    )
    check(counter["payment_days"] == 15, "counter pays in 15 days")
    check(
        o["status"] == "queued" and "20%" in o["queue_reason"],
        f"counter changes value by more than policy allows → approval queue ({o['queue_reason']})",
    )

    print("SLACK · the Human Liaison asks the gifting buyer; the buyer approves")
    ask = next(m for m in await outbox(s) if "countered order" in m["message"]["text"])
    check(ask["message"]["channel"] == "slack", f"asked on Slack ({ask['message']['to']})")
    await reply(s, ask, "approve")
    check(bool((await tick(s))["answered"]), "Liaison read the buyer's reply")
    o = await retailer_order(s, o["order_id"])
    check(o["status"] == "awaiting_vendor", "counter accepted; waits for Maya")

    print("CONSENT · Maya hears the read-back and says yes")
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

    print("TERMS · the buyer moves to standard 15-day terms (Studio)")
    await use_terms(s, "gift-standard-v1")

    print("GREEN · Hub 7 orders 10 candles @ 11:00 (inside Maya's rules, under auto-accept)")
    o = await place(s, "store:hub-7", day(1))
    check(
        o["status"] == "confirmed",
        f"retailer order confirmed without asking Maya ({o['status']})",
    )
    check(
        [d["status"] for d in o["decisions"]] == ["green"],
        "Guardian decision is green, first time",
    )

    print("AMBER · Hub 9 orders 30 candles @ 12:00 (inside rules, over auto-accept)")
    o = await place(s, "store:hub-9", day(3))
    check([d["status"] for d in o["decisions"]] == ["amber"], "Guardian returns amber")
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
