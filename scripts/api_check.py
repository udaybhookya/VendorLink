"""Exercise every API route of every service through a realistic v1 journey.

    uv run python scripts/api_check.py           # in-process: also proves every route was hit
    uv run python scripts/api_check.py --live    # against `make dev` (+ SSE streams)

Every request (including service-to-service calls, in-process) is recorded and matched to its
route template. The run fails on an unexpected status or, in-process, on any route nobody hit.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx

from scripts._demo import MAYA, RETAILER, DemoError, call, day, deal, place, reset
from vl_core.a2a import set_transport
from vl_core.settings import Settings, get_settings

# Server-Sent Event streams never finish, so they cannot be read through the in-process
# transport; --live reads their first bytes instead.
STREAMING = {
    ("gateway", "GET /events"),
    ("gateway", "GET /events/stream"),
    ("vendor", "GET /vendors/{vendor_id}/events"),
    ("retailer", "GET /retailers/{retailer_id}/events"),
}
OPERATOR_HEADERS = {"X-VL-Operator-Token": ""}
PIXEL = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
).decode()


@dataclass
class Recorder:
    seen: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))
    services: dict[str, str] = field(default_factory=dict)  # netloc → service name
    templates: dict[str, list[tuple[str, re.Pattern[str]]]] = field(default_factory=dict)

    def add_service(self, name: str, base_url: str, routes: list[str]) -> None:
        self.services[httpx.URL(base_url).netloc.decode()] = name
        compiled = []
        for route in routes:
            method, path = route.split(" ", 1)
            regex = "^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"
            compiled.append((route, re.compile(f"{method} {regex[1:]}")))
        # literal paths beat templates (/agents/status before /agents/{agent_id})
        self.templates[name] = sorted(compiled, key=lambda c: (c[0].count("{"), -len(c[0])))

    def record(self, request: httpx.Request, status: int) -> None:
        service = self.services.get(request.url.netloc.decode())
        if service is None:
            return
        line = f"{request.method} {request.url.path}"
        for route, pattern in self.templates[service]:
            if pattern.match(line):
                self.seen[(service, route)].add(status)
                return


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, recorder: Recorder) -> None:
        self.inner = inner
        self.recorder = recorder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        self.recorder.record(request, response.status_code)
        return response


def routes_of(app: Any) -> list[str]:
    from fastapi.routing import APIRoute

    return sorted(
        f"{m} {r.path}" for r in app.routes if isinstance(r, APIRoute) for m in (r.methods or ())
    )


async def journey(s: Settings, operator_token: str) -> None:
    """One realistic pass over the whole product, A then B."""
    ops = {"X-VL-Operator-Token": operator_token}
    gw, vs, rs, mk = s.trust_gateway_url, s.vendor_svc_url, s.retailer_svc_url, s.mocks_url

    # health and public reads
    for base in (gw, vs, rs, mk):
        await call(base, "GET", "/healthz")
    await call(gw, "GET", "/.well-known/gateway-key")
    await reset(s, "A")  # POST /dev/reset on each service; services register agents

    # supplier joins: options, directory, register, documents, rules, apply
    await call(vs, "GET", "/options")
    await call(vs, "GET", "/directory/retailers")
    vendor = await call(
        vs,
        "POST",
        "/vendors",
        json={
            "display_name": "API Check Farms",
            "legal_name": "Maya Thomas",
            "business_profile": "fresh_produce",
            "country": "IN",
            "language": "en",
        },
    )
    vid = vendor["id"]
    await call(vs, "GET", "/vendors")
    await call(vs, "GET", f"/vendors/{vid}/me")
    await call(vs, "PATCH", f"/vendors/{vid}/profile", json={"display_name": "API Check Farm"})
    await call(vs, "GET", f"/vendors/{vid}/required-docs")
    await call(rs, "GET", f"/retailers/{RETAILER}/directory")
    await call(rs, "POST", f"/retailers/{RETAILER}/suppliers/{vid}/invite", json={"message": "hi"})
    await call(vs, "GET", f"/vendors/{vid}/invites")
    for doc_type, number in (
        ("aadhaar", "234567890123"),
        ("ifsc_account", "SBIN0001234:123456789012"),
    ):
        await call(
            vs, "POST", f"/vendors/{vid}/documents", json={"doc_type": doc_type, "number": number}
        )
    rules = await call(
        vs,
        "POST",
        f"/vendors/{vid}/rules/draft",
        json={
            "skus": [
                {"sku": "spinach", "unit": "bunch", "price_floor": "30", "max_qty_per_day": "100"}
            ],
            "pickup_windows": [{"start": "06:00", "end": "09:00"}],
            "payment_days_max": 7,
            "approved_buyers": [RETAILER],
            "auto_accept_max_value": "2000",
        },
    )
    await call(
        vs,
        "POST",
        f"/vendors/{vid}/rules/consent",
        json={"subject_hash": rules["subject_hash"], "transcript": "yes", "language": "en"},
    )
    await call(vs, "GET", f"/vendors/{vid}/rules")
    await call(vs, "POST", f"/vendors/{vid}/apply", params={"retailer_id": RETAILER})
    queue = await call(rs, "GET", f"/retailers/{RETAILER}/queue")
    item = next(q for q in queue if q["ref_id"] == vid)
    await call(rs, "POST", f"/retailers/{RETAILER}/queue/{item['id']}/approve")
    await call(rs, "GET", f"/retailers/{RETAILER}/vendors")
    await call(rs, "POST", f"/retailers/{RETAILER}/terms/{vid}")
    [offered] = await call(vs, "GET", f"/vendors/{vid}/terms")
    await call(vs, "POST", f"/vendors/{vid}/terms/{offered['terms']['terms_id']}/accept")
    await call(rs, "GET", f"/retailers/{RETAILER}/terms-responses")

    # products: photo → draft → confirm
    await call(
        vs,
        "POST",
        f"/vendors/{vid}/chat/photo",
        json={"doc_type": "photo", "data_b64": PIXEL, "content_type": "image/png"},
    )
    draft = await call(
        vs,
        "POST",
        f"/vendors/{vid}/listings/draft",
        json={
            "vendor_id": vid,
            "language": "en",
            "transcript": "40 bunch of spinach at 35",
            "photo_b64": PIXEL,
            "photo_mime": "image/png",
        },
    )
    listing_id = draft["result"]["listing"]["id"]
    await call(vs, "POST", f"/vendors/{vid}/listings/{listing_id}/confirm")
    await call(vs, "GET", f"/vendors/{vid}/listings")
    await call(rs, "GET", f"/retailers/{RETAILER}/listings")

    # the order loop with Maya: red → counter → amber → yes; amber → no
    order = await place(s, "store:hub-4", day(2))
    view = await deal(s, order["order_id"])
    await call(vs, "GET", f"/vendors/{MAYA}/deals/{order['order_id']}")
    await call(
        vs,
        "POST",
        f"/vendors/{MAYA}/deals/{order['order_id']}/consent",
        json={"subject_hash": view["subject_hash"], "transcript": "అవును", "language": "te"},
    )
    second = await place(s, "store:hub-9", day(3))
    await call(vs, "POST", f"/vendors/{MAYA}/deals/{second['order_id']}/decline")
    await call(rs, "GET", f"/retailers/{RETAILER}/orders")
    await call(vs, "POST", f"/vendors/{MAYA}/chat", json={"text": "deals"})
    for path in ("activity", "today"):
        await call(vs, "GET", f"/vendors/{MAYA}/{path}")

    # configuration studio and the A → B switch
    await call(rs, "GET", "/retailers")
    await call(rs, "GET", "/config/presets")
    await call(rs, "GET", "/config/retailer_policies")
    policy = await call(rs, "GET", "/config/retailer_policies/default_qc")
    await call(
        rs,
        "PUT",
        "/config/retailer_policies/default_qc",
        json={**policy, "auto_accept_counter_max_delta": 0.25},
    )
    await call(rs, "GET", "/registry/cards")
    await call(rs, "GET", "/registry/agents")
    await call(rs, "POST", f"/retailers/{RETAILER}/config/activate", json={"preset": "B"})
    await call(rs, "GET", f"/retailers/{RETAILER}/config/active")
    await call(vs, "POST", "/vendors/vendor:sara/apply")  # P-IRT registry, bank penny-drop

    # mocks called directly
    await call(mk, "GET", "/fssai/10019022000123", params={"name": "Maya Thomas"})
    await call(mk, "GET", "/demand", params={"store": "store:hub-4", "for_date": "2026-10-15"})
    await call(
        mk,
        "POST",
        "/receiving",
        json={
            "order_id": order["order_id"],
            "received_qty": "300",
            "rejected_qty": "0",
            "received_at": "2026-10-15T06:30:00+05:30",
        },
    )

    # trust, control plane and observability
    for path in (
        "/credentials",
        "/agents",
        "/agents/status",
        "/consents",
        "/mandates/order",
        "/parties/config",
        "/security-events",
        "/audit",
        "/events/recent",
        "/observability/agents",
        "/observability/safety",
        "/observability/quality",
    ):
        await call(gw, "GET", path)
    await call(gw, "GET", f"/agents/{vendor['agent_id']}")
    await call(gw, "GET", f"/mandates/standing/{MAYA}")
    traces = await call(gw, "GET", "/observability/traces")
    await call(gw, "GET", f"/observability/traces/{traces[0]['trace_id']}")
    body = {"actor": "api-check", "reason": "route check"}
    await call(gw, "POST", "/control/agents/terms-agent/suspend", headers=ops, json=body)
    await call(gw, "POST", "/control/agents/terms-agent/resume", headers=ops, json=body)
    await call(gw, "POST", "/control/tenants/vendor/freeze", headers=ops, json=body)
    await call(gw, "POST", "/control/tenants/vendor/unfreeze", headers=ops, json=body)
    await call(
        gw, "POST", "/control/agents/retailer-agent:freshcart/rotate_key", headers=ops, json=body
    )
    await call(rs, "POST", f"/retailers/{RETAILER}/config/activate", json={"preset": "A"})
    # the host rotates on its next status refresh; the order then runs on the new key
    assert (await place(s, "store:hub-7", day(4)))["status"] == "confirmed"
    await call(
        gw,
        "POST",
        f"/agents/{vendor['agent_id']}/revoke",
        headers={"X-VL-Admin-Token": s.vl_admin_token},
        json={"reason": "route check"},
    )

    # human in the loop: Slack approval via the outbox, console answer, batch job, vendor answer
    policy = await call(rs, "GET", "/config/retailer_policies/default_qc")
    await call(
        rs,
        "PUT",
        "/config/retailer_policies/default_qc",
        json={**policy, "auto_order_max_value": "13000"},
    )
    await call(rs, "POST", f"/retailers/{RETAILER}/config/activate", json={"preset": "A"})
    run = await call(
        rs,
        "POST",
        f"/retailers/{RETAILER}/jobs/replenishment-run",
        json={"for_date": day(6).isoformat(), "vendor_id": MAYA},
    )
    await call(rs, "GET", f"/retailers/{RETAILER}/jobs")
    await call(rs, "GET", f"/retailers/{RETAILER}/jobs/{run['id']}")
    questions = await call(vs, "GET", f"/vendors/{MAYA}/questions")
    supply = next(q for q in questions if q["request"]["field"].startswith("supply:"))
    await call(vs, "POST", f"/vendors/{MAYA}/questions/{supply['id']}/answer", json={"text": "45"})
    await call(rs, "GET", f"/retailers/{RETAILER}/info-responses")
    box = await call(rs, "GET", "/dev/outbox")
    ask = next(m for m in box if "auto-order limit" in m["message"]["text"])
    await call(
        rs,
        "POST",
        f"/dev/outbox/{ask['message']['channel']}/{ask['id']}/reply",
        json={"text": "approve", "by": "U-api-check"},
    )
    await call(rs, "POST", "/dev/tick")
    await call(vs, "POST", "/dev/tick")
    await call(vs, "GET", "/dev/outbox")
    tasks = await call(rs, "GET", f"/retailers/{RETAILER}/tasks", params={"status": "open"})
    if tasks:
        choice = (tasks[0]["request"]["choices"] or [{"id": "ok"}])[0]["id"]
        await call(
            rs,
            "POST",
            f"/retailers/{RETAILER}/tasks/{tasks[0]['id']}/answer",
            json={"text": choice, "by": "console"},
        )
    else:
        await call(
            rs,
            "POST",
            f"/retailers/{RETAILER}/tasks/none/answer",
            expect=404,
            json={"text": "approve"},
        )
    await call(gw, "GET", "/observability/humans")

    # the audit chain verifies, and catches tampering
    assert (await call(gw, "GET", "/audit/verify"))["ok"]
    await call(gw, "POST", "/dev/tamper/3")
    assert not (await call(gw, "GET", "/audit/verify"))["ok"]
    await reset(s, "A")


async def check_streams(s: Settings) -> list[str]:
    problems = []
    urls = [
        f"{s.trust_gateway_url}/events",
        f"{s.trust_gateway_url}/events/stream",
        f"{s.vendor_svc_url}/vendors/{MAYA}/events",
        f"{s.retailer_svc_url}/retailers/{RETAILER}/events",
    ]
    async with httpx.AsyncClient(timeout=5) as client:
        for url in urls:
            try:
                async with client.stream("GET", url) as response:
                    first = await anext(response.aiter_text())
                    if response.status_code != 200 or "connected" not in first:
                        problems.append(f"{url}: {response.status_code} {first[:40]!r}")
            except Exception as exc:
                problems.append(f"{url}: {exc!r}")
    return problems


def report(recorder: Recorder, all_routes: dict[str, list[str]], live: bool) -> list[str]:
    problems = []
    for service, routes in all_routes.items():
        hit = sum(1 for r in routes if (service, r) in recorder.seen)
        print(f"\n{service}: {hit}/{len(routes)} routes exercised")
        for route in routes:
            statuses = recorder.seen.get((service, route))
            if statuses:
                bad = [c for c in statuses if c >= 500]
                mark = "✗" if bad else "✓"
                print(f"  {mark} {route:60} {sorted(statuses)}")
                if bad:
                    problems.append(f"{service} {route}: {sorted(statuses)}")
            elif (service, route) in STREAMING:
                print(f"  ~ {route:60} SSE (checked with --live)")
            else:
                print(f"  – {route:60} not exercised")
                if not live:
                    problems.append(f"{service} {route}: never exercised")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    recorder = Recorder()
    if args.live:
        settings = get_settings()
        from mocks.app import app as mocks_app
        from retailer_svc.app import app as retailer_app
        from trust_gateway.app import app as gateway_app
        from vendor_svc.app import app as vendor_app

        apps = {
            "gateway": gateway_app,
            "vendor": vendor_app,
            "retailer": retailer_app,
            "mocks": mocks_app,
        }
        set_transport(RecordingTransport(httpx.AsyncHTTPTransport(), recorder))
    else:
        from scripts._stack import APPS, AppRouter, build_stack

        settings = build_stack()
        apps = dict(APPS)
        inner = AppRouter(
            {
                settings.trust_gateway_url: apps["gateway"],
                settings.vendor_svc_url: apps["vendor"],
                settings.retailer_svc_url: apps["retailer"],
                settings.mocks_url: apps["mocks"],
            }
        )
        set_transport(RecordingTransport(inner, recorder))
    urls = {
        "gateway": settings.trust_gateway_url,
        "vendor": settings.vendor_svc_url,
        "retailer": settings.retailer_svc_url,
        "mocks": settings.mocks_url,
    }
    all_routes = {name: routes_of(app) for name, app in apps.items()}
    for name, routes in all_routes.items():
        recorder.add_service(name, urls[name], routes)
    try:
        asyncio.run(journey(settings, settings.vl_operator_token))
        problems = report(recorder, all_routes, args.live)
        if args.live:
            set_transport(None)
            problems += asyncio.run(check_streams(settings))
    except (DemoError, AssertionError, StopIteration) as exc:
        print(f"API CHECK FAILED during the journey: {exc!r}")
        return 1
    finally:
        set_transport(None)
    total = sum(len(r) for r in all_routes.values())
    if problems:
        print(f"\nAPI CHECK FAILED ({len(problems)} problems):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"\nAPI CHECK PASSED: {total} routes, all exercised or streamed, no server errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
