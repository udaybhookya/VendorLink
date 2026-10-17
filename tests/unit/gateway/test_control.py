"""Control plane: every action works, is enforced on every path, and names the person."""

from __future__ import annotations

import pytest

from tests.unit.gateway.conftest import FRESHCART, GW, MAYA, Env, order
from vl_core.a2a import GatewayError, make_client
from vl_core.schemas import Decision

OPERATOR = {"X-VL-Operator-Token": "test-operator"}


async def control(
    path: str, body: dict | None = None, headers: dict | None = None
) -> tuple[int, dict]:  # type: ignore[type-arg]
    async with make_client(GW) as client:
        r = await client.post(
            path,
            json=body or {"actor": "asha@freshcart", "reason": "test"},
            headers=OPERATOR if headers is None else headers,
        )
    return r.status_code, r.json()


async def propose(env: Env) -> Decision:
    return await env.retailer.relay(
        MAYA,
        "order.propose",
        order(),
        on_behalf_of="retailer:freshcart",
        response_model=Decision,
    )


async def test_operator_token_required(env: Env) -> None:
    status, _ = await control(f"/control/agents/{MAYA}/suspend", headers={})
    assert status == 403


async def test_suspend_and_resume_on_every_path(env: Env) -> None:
    assert (await control(f"/control/agents/{MAYA}/suspend"))[0] == 200
    with pytest.raises(GatewayError) as err:  # as caller
        await env.vendor.token("order.vendor_signed", FRESHCART)
    assert err.value.code == "suspended"
    with pytest.raises(GatewayError) as err:  # as target
        await propose(env)
    assert err.value.code == "target_suspended"
    await control(f"/control/agents/{MAYA}/resume")
    assert (await propose(env)).status == "green"


async def test_revoke_is_final(env: Env) -> None:
    await control(f"/control/agents/{MAYA}/revoke")
    status, body = await control(f"/control/agents/{MAYA}/resume")
    assert status == 409 and body["detail"]["error"] == "revoked_is_final"


async def test_permissions_narrow_what_an_agent_may_do(env: Env) -> None:
    await control(
        f"/control/agents/{FRESHCART}/set_permissions",
        {"actor": "asha", "reason": "orders only", "permissions": ["terms.propose"]},
    )
    with pytest.raises(GatewayError) as err:
        await propose(env)
    assert err.value.code == "permission_denied"
    cred = await env.gateway.store.credentials.get(FRESHCART)
    assert cred is not None and cred.status == "active"  # narrowed, not punished


async def test_end_sessions_voids_outstanding_tokens(env: Env) -> None:
    token = await env.retailer.token("order.propose", MAYA)
    await __import__("asyncio").sleep(0.005)  # the operator acts after the token was issued
    await control(f"/control/agents/{FRESHCART}/end_sessions")
    from vl_core.a2a import make_envelope

    envelope = make_envelope(
        skill="order.propose",
        from_agent=FRESHCART,
        to_agent=MAYA,
        on_behalf_of="retailer:freshcart",
        body=order(),
        jti=token.jti,
    )
    with pytest.raises(GatewayError) as err:
        await env.retailer._post(
            f"/relay/{MAYA}/order.propose", envelope, jti=token.jti, token=token.token
        )
    assert err.value.code == "session_ended"


async def test_freeze_a_whole_tenant(env: Env) -> None:
    await control("/control/tenants/vendor/freeze")
    with pytest.raises(GatewayError) as err:
        await propose(env)
    assert err.value.code == "target_frozen"
    snapshot = await env.gateway.control.snapshot()
    assert "vendor" in snapshot.frozen
    maya = next(a for a in snapshot.agents if a.agent_id == MAYA)
    assert (maya.status, maya.effective) == ("active", "suspended")
    await control("/control/tenants/vendor/unfreeze")
    assert (await propose(env)).status == "green"


async def test_actions_name_the_person_in_audit_and_events(env: Env) -> None:
    await control(
        f"/control/agents/{MAYA}/quarantine", {"actor": "sam@platform", "reason": "odd orders"}
    )
    audit = [r for r in await env.gateway.audit.records() if r.action == "control.quarantine"]
    assert audit and audit[0].actor_agent_id == "operator:sam@platform"
    events = env.gateway.control.recent(type="control")
    assert events[0].actor == "sam@platform" and events[0].attrs["reason"] == "odd orders"


async def test_unified_events_for_logins_calls_and_decisions(env: Env) -> None:
    await propose(env)
    kinds = {(e.type, e.action) for e in env.gateway.control.recent()}
    assert {
        ("login", "order.propose"),
        ("call", "order.propose"),
        ("decision", "order.propose"),
    } <= kinds
    call = next(e for e in env.gateway.control.recent(type="call"))
    assert call.latency_ms is not None and call.agent_id == FRESHCART and call.target == MAYA


async def test_rotate_key_needs_own_key(env: Env) -> None:
    status, body = await control(f"/control/agents/{FRESHCART}/rotate_key")
    assert status == 200 and body["agent"]["key_rotation_requested"] is True


async def test_tokens_issued_after_ending_sessions_still_work(env: Env) -> None:
    await control(f"/control/agents/{FRESHCART}/end_sessions")
    await __import__("asyncio").sleep(0.005)
    assert (await propose(env)).status == "green"
