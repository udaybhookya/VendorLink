"""Deny by default: every skill in the table refuses the reverse direction."""

from __future__ import annotations

import pytest

from tests.unit.gateway.conftest import FRESHCART, MAYA, Env
from trust_gateway.policy import SKILL_POLICY
from vl_core.a2a import GatewayError


@pytest.mark.parametrize("skill", sorted(SKILL_POLICY))
async def test_wrong_direction_is_refused(env: Env, skill: str) -> None:
    rule = SKILL_POLICY[skill]
    # Ask from the party type the rule does NOT allow as sender.
    client, aud = (env.vendor, FRESHCART) if rule.from_party == "retailer" else (env.retailer, MAYA)
    with pytest.raises(GatewayError) as err:
        await client.token(skill, aud)
    assert err.value.code == "out_of_scope"


def test_every_skill_has_typed_body_and_response() -> None:
    for skill, rule in SKILL_POLICY.items():
        assert rule.body.__module__.startswith("vl_core.schemas"), skill
        assert rule.response.__module__.startswith("vl_core.schemas"), skill
