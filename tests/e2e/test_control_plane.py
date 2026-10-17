"""Control plane across all services: tracing, in-process agents, scores, rotation."""

from __future__ import annotations

from typing import Any

from scripts._demo import RETAILER, call, day, place, reset
from scripts._stack import APPS
from vl_core.settings import Settings

OPS = {"X-VL-Operator-Token": "inprocess-operator"}


async def control(s: Settings, path: str, **extra: Any) -> Any:
    return await call(
        s.trust_gateway_url,
        "POST",
        path,
        headers=OPS,
        json={"actor": "asha@freshcart", "reason": "e2e", **extra},
    )


async def test_hosted_agents_are_registered_and_visible(stack: Settings) -> None:
    await reset(stack, "A")
    agents = {a["agent_id"]: a for a in await call(stack.trust_gateway_url, "GET", "/agents")}
    assert agents["host:retailer-svc"]["kind"] == "host"
    grader = agents["craft-quality-checker"]
    assert grader["kind"] == "specialist" and grader["host"] == "retailer-svc"
    assert grader["key_fingerprint"] == agents["host:retailer-svc"]["key_fingerprint"]
    assert agents["guardian"]["host"] == "vendor-svc"


async def test_one_trace_follows_an_order_across_services(stack: Settings) -> None:
    await reset(stack, "A")
    order = await place(stack, "store:hub-4", day(2))
    traces = await call(stack.trust_gateway_url, "GET", "/observability/traces")
    full = next(t for t in traces if "orchestrator" in t["agents"] and "guardian" in t["agents"])
    assert {"replenishment-planner", "retailer-agent:freshcart"} <= set(full["agents"])
    view = await call(stack.trust_gateway_url, "GET", f"/observability/traces/{full['trace_id']}")
    actions = [e["action"] for e in view["events"]]
    assert "orchestrator.plan_orders" in actions and "guardian.decide" in actions
    decision = next(e for e in view["events"] if e["action"] == "guardian.decide")
    assert decision["type"] == "decision" and decision["attrs"]["status"] == "red"
    assert decision["on_behalf_of"] == "vendor:maya"
    assert order["status"] == "queued"  # the counter changes the value past policy: buyer decides


async def test_suspending_a_specialist_stops_it_inside_retailer_svc(stack: Settings) -> None:
    await reset(stack, "A")
    await control(stack, "/control/agents/replenishment-planner/suspend")
    r = await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/orders/place",
        expect=None,
        json={
            "vendor_id": "vendor:maya",
            "store_id": "store:hub-7",
            "for_date": day(1).isoformat(),
        },
    )
    assert r["detail"]["error"] in ("agent_blocked", "specialist_failed"), r
    denied = await call(
        stack.trust_gateway_url,
        "GET",
        "/events/recent",
        params={"agent_id": "replenishment-planner", "type": "permission_check"},
    )
    assert denied and denied[0]["outcome"] == "denied"
    await control(stack, "/control/agents/replenishment-planner/resume")
    assert (await place(stack, "store:hub-7", day(1)))["status"] == "confirmed"


async def test_frozen_vendor_tenant_stops_the_guardian_in_process(stack: Settings) -> None:
    await reset(stack, "A")
    await control(stack, "/control/tenants/vendor/freeze")
    r = await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/orders/place",
        expect=None,
        json={
            "vendor_id": "vendor:maya",
            "store_id": "store:hub-7",
            "for_date": day(1).isoformat(),
        },
    )
    assert "target_frozen" in str(r)


async def test_operator_requested_key_rotation_happens_on_the_host(stack: Settings) -> None:
    await reset(stack, "A")
    before = await call(stack.trust_gateway_url, "GET", "/agents/retailer-agent:freshcart")
    await control(stack, "/control/agents/retailer-agent:freshcart/rotate_key")
    await APPS["retailer"].state.operations.control.status("orchestrator")  # next status refresh
    after = await call(stack.trust_gateway_url, "GET", "/agents/retailer-agent:freshcart")
    assert after["key_fingerprint"] != before["key_fingerprint"]
    assert after["key_rotation_requested"] is False
    assert (await place(stack, "store:hub-7", day(1)))["status"] == "confirmed"  # new key works


async def test_observability_scores(stack: Settings) -> None:
    from scripts import attack_impostor

    await attack_impostor.run(stack)
    await place(stack, "store:hub-7", day(1))
    observed = {
        o["agent"]["agent_id"]: o
        for o in await call(stack.trust_gateway_url, "GET", "/observability/agents")
    }
    maya = observed["vendor-agent:maya"]
    assert maya["effective_status"] == "active" and 85 <= maya["trust_score"] < 100
    assert any(f["name"] == "impersonation attempts" for f in maya["trust_factors"])
    assert observed["orchestrator"]["metrics"]["calls"] >= 1
    assert observed["orchestrator"]["live"] is True
    safety = await call(stack.trust_gateway_url, "GET", "/observability/safety")
    assert safety["security_events"]["bad_signature"] >= 1
    quality = await call(stack.trust_gateway_url, "GET", "/observability/quality")
    assert any(q["agent_id"] == "replenishment-planner" and q["calls"] >= 1 for q in quality)


async def test_supplier_registers_gets_invited_applies_and_declines(stack: Settings) -> None:
    await reset(stack, "A")
    vendor = await call(
        stack.vendor_svc_url,
        "POST",
        "/vendors",
        json={
            "display_name": "Meena Crafts",
            "legal_name": "Maya Thomas",
            "business_profile": "handmade_gifts",
            "country": "IN",
            "language": "te",
        },
    )
    vid = vendor["id"]
    assert vid == "vendor:meena-crafts" and vendor["agent_id"] == "vendor-agent:meena-crafts"
    gw_agent = await call(stack.trust_gateway_url, "GET", f"/agents/{vendor['agent_id']}")
    assert gw_agent["status"] == "active" and gw_agent["key_fingerprint"].startswith("SHA256:")

    directory = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/directory")
    assert next(d for d in directory if d["party_id"] == vid)["status"] == "not_connected"
    await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/suppliers/{vid}/invite",
        json={"message": "Supply candles to our gifting desk?"},
    )
    invites = await call(stack.vendor_svc_url, "GET", f"/vendors/{vid}/invites")
    assert invites[0]["retailer_id"] == RETAILER and invites[0]["retailer_name"]

    options = await call(stack.vendor_svc_url, "GET", "/options")
    assert {c["code"] for c in options["countries"]} == {"IN", "ID"}
    docs = await call(stack.vendor_svc_url, "GET", f"/vendors/{vid}/required-docs")
    assert set(docs["roles"]) == {"identity", "bank_account"}
    for doc_type, number in (
        ("aadhaar", "234567890123"),
        ("ifsc_account", "SBIN0001234:123456789012"),
    ):
        await call(
            stack.vendor_svc_url,
            "POST",
            f"/vendors/{vid}/documents",
            json={"doc_type": doc_type, "number": number},
        )
    check = await call(
        stack.vendor_svc_url, "POST", f"/vendors/{vid}/apply", params={"retailer_id": RETAILER}
    )
    assert check["passed"], check
    directory = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/directory")
    assert next(d for d in directory if d["party_id"] == vid)["status"] == "pending_approval"


async def test_vendor_saying_no_closes_the_retailer_order(stack: Settings) -> None:
    await reset(stack, "A")
    order = await place(stack, "store:hub-9", day(3))
    assert order["status"] == "awaiting_vendor"
    await call(
        stack.vendor_svc_url, "POST", f"/vendors/vendor:maya/deals/{order['order_id']}/decline"
    )
    orders = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/orders")
    assert next(o for o in orders if o["order_id"] == order["order_id"])["status"] == "declined"
