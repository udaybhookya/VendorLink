"""An impostor claims to be Maya's agent. Every path must be BLOCKED and recorded.

uv run python scripts/attack_impostor.py [--live]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal

from scripts._demo import MAYA, RETAILER, DemoError, call, reset
from vl_core.a2a import GatewayClient, GatewayError, make_envelope
from vl_core.crypto import LocalSigner, canonical_json, jwt_encode
from vl_core.schemas import OrderLine, OrderProposal, SignedOrder
from vl_core.settings import Settings, get_settings

VICTIM = "vendor-agent:maya"


def forged_order() -> SignedOrder:
    """What an impostor would like the retailer to believe Maya signed."""
    proposal = OrderProposal(
        order_id="ord-forged",
        version=1,
        retailer_id=RETAILER,
        vendor_id=MAYA,
        store_id="store:hub-4",
        lines=(OrderLine(sku="spinach", qty=Decimal(1000), unit="bunch", price=Decimal(1)),),
        pickup_at=datetime.now(UTC),
        payment_days=90,
        currency="INR",
        terms_version="qc-standard-v1",
    )
    return SignedOrder(proposal=proposal, vendor_sig="forged")


TARGET = "retailer-agent:freshcart"


def blocked(label: str, ok: bool) -> None:
    print(f"  {'BLOCKED' if ok else 'NOT BLOCKED'} · {label}")
    if not ok:
        raise DemoError(label)


async def run(s: Settings) -> None:
    await reset(s, "A")
    impostor_key = LocalSigner()
    impostor = GatewayClient(s.trust_gateway_url, VICTIM, impostor_key)

    print("1. Impostor asks the gateway for a token as Maya's agent (own key, not registered)")
    try:
        await impostor.token("order.vendor_signed", TARGET)
        blocked("token request with a forged signature", False)
    except GatewayError as exc:
        blocked(f"token request → {exc.status} {exc.code}", exc.code == "bad_signature")

    print("2. Impostor mints its own token and calls the relay")
    fake = await jwt_encode(
        impostor_key,
        "platform:gateway",
        {
            "sub": VICTIM,
            "obo": MAYA,
            "scope": "order.vendor_signed",
            "aud": TARGET,
            "jti": "forged-1",
            "exp": int(time.time()) + 300,
        },
    )
    try:
        await impostor._post(
            f"/relay/{TARGET}/order.vendor_signed",
            make_envelope(
                skill="order.vendor_signed",
                from_agent=VICTIM,
                to_agent=TARGET,
                on_behalf_of=MAYA,
                body=forged_order(),
                jti="forged-1",
            ),
            jti="forged-1",
            token=fake,
        )
        blocked("relay with a self-made token", False)
    except GatewayError as exc:
        blocked(f"relay → {exc.status} {exc.code}", exc.code == "bad_token")

    print("3. Impostor skips the gateway and calls the retailer's A2A endpoint directly")
    envelope = make_envelope(
        skill="order.vendor_signed",
        from_agent=VICTIM,
        to_agent=TARGET,
        on_behalf_of=MAYA,
        body=forged_order(),
    )
    r = await call(
        s.retailer_svc_url,
        "POST",
        f"/a2a/{TARGET}/order.vendor_signed",
        expect=None,
        content=canonical_json(envelope),
    )
    blocked(f"direct call → {r['detail']['error']}", r["detail"]["error"] == "not_from_gateway")

    events = await call(s.trust_gateway_url, "GET", "/security-events")
    types = [e["type"] for e in events]
    print(f"Security events: {types}")
    if not {"bad_signature", "bad_token"} <= set(types):
        raise DemoError("security events missing")
    creds = await call(s.trust_gateway_url, "GET", "/credentials")
    victim = next(c for c in creds if c["agent_id"] == VICTIM)
    if victim["status"] != "active":
        raise DemoError("the real Maya was locked out by an impostor")
    print("  Maya's real agent is still active (unproven identity never triggers revocation)")
    denied = [
        r for r in await call(s.trust_gateway_url, "GET", "/audit") if r["outcome"] == "denied"
    ]
    print(f"  {len(denied)} denied attempts are in the hash-chained audit log")
    print("IMPOSTOR BLOCKED")


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
