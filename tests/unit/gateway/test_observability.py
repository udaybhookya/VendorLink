"""Observability views: overview, connections, activity timings, tokens, auth and agent detail."""

from __future__ import annotations

import pytest

from tests.unit.gateway.conftest import FRESHCART, GW, MAYA, Env, order
from trust_gateway import observability
from vl_core.a2a import GatewayClient, GatewayError, make_client
from vl_core.crypto import LocalSigner
from vl_core.schemas import Decision


async def propose(env: Env) -> Decision:
    return await env.retailer.relay(
        MAYA, "order.propose", order(), on_behalf_of="retailer:freshcart", response_model=Decision
    )


async def get(path: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    async with make_client(GW) as client:
        r = await client.get(path)
    return r.status_code, r.json()


async def test_relayed_messages_form_a_connection(env: Env) -> None:
    await propose(env)
    await propose(env)
    [conn] = await observability.connections(env.gateway)
    assert (conn.vendor_agent, conn.retailer_agent) == (MAYA, FRESHCART)
    assert (conn.vendor, conn.retailer) == ("vendor:maya", "retailer:freshcart")
    assert conn.messages == conn.to_vendor == 2 and conn.to_retailer == 0
    assert conn.skills == {"order.propose": 2} and conn.decisions == {"green": 2}
    assert conn.p95_ms is not None and conn.last_seen is not None


async def test_overview_counts_sides_messages_and_logins(env: Env) -> None:
    await propose(env)
    view = await observability.overview(env.gateway)
    assert view.sides["vendor"].total == 1 and view.sides["retailer"].total == 1
    assert view.sides["vendor"].talking == view.sides["retailer"].talking == 1
    assert view.connections == 1 and view.messages == 1 and view.logins == 1
    assert view.authn_failures == view.authz_denials == 0


async def test_authn_and_authz_are_told_apart(env: Env) -> None:
    impostor = GatewayClient(GW, MAYA, LocalSigner())  # Maya's id, someone else's key
    with pytest.raises(GatewayError):
        await impostor.token("order.vendor_signed", FRESHCART)
    async with make_client(GW) as client:
        await client.post(
            f"/control/agents/{FRESHCART}/set_permissions",
            json={"actor": "asha", "reason": "terms only", "permissions": ["terms.propose"]},
            headers={"X-VL-Operator-Token": "test-operator"},
        )
    with pytest.raises(GatewayError):
        await propose(env)
    view = await observability.overview(env.gateway)
    assert (view.authn_failures, view.authz_denials) == (1, 1)
    maya = await observability.agent_detail(env.gateway, MAYA)
    freshcart = await observability.agent_detail(env.gateway, FRESHCART)
    assert maya is not None and freshcart is not None
    assert maya.auth.authn_failures == {"bad_signature": 1} and not maya.auth.authz_denials
    assert freshcart.auth.authz_denials == {"permission_denied": 1}
    assert freshcart.auth.permissions == ["terms.propose"]


async def test_tokens_add_up_per_agent_step_and_overall(env: Env) -> None:
    for tokens_in in (100, 300):
        env.gateway.control.emit(
            "call",
            "concierge",
            "concierge.chat",
            latency_ms=900.0,
            attrs={"tokens_in": tokens_in, "tokens_out": 50, "model_calls": 1},
        )
    m = observability.metrics_for("concierge", list(env.gateway.control.events), order().pickup_at)
    assert (m.tokens_in, m.tokens_out, m.model_calls) == (400, 100, 2)
    view = await observability.overview(env.gateway)
    assert (view.tokens_in, view.tokens_out, view.model_calls) == (400, 100, 2)
    acts = await observability.activities(env.gateway)
    [chat] = [s for s in acts.steps if s.activity == "concierge.chat"]
    assert chat.count == 2 and chat.tokens_in == 400 and chat.p95_ms == 900.0


async def test_activities_time_relayed_steps(env: Env) -> None:
    await propose(env)
    acts = await observability.activities(env.gateway)
    [step] = [s for s in acts.steps if s.activity == "relay:order.propose"]
    assert step.count == 1 and step.p50_ms is not None and step.agents == [FRESHCART]


async def test_agent_detail_route(env: Env) -> None:
    await propose(env)
    status, body = await get(f"/observability/agents/{MAYA}")
    assert status == 200
    assert body["observation"]["agent"]["agent_id"] == MAYA
    assert len(body["connections"]) == 1 and body["events"]
    assert (await get("/observability/agents/nobody"))[0] == 404
    for path in (
        "/observability/overview",
        "/observability/connections",
        "/observability/activities",
    ):
        assert (await get(path))[0] == 200
