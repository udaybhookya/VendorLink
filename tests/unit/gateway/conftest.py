from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI

from scripts._stack import AppRouter
from trust_gateway.app import create_app
from trust_gateway.gateway import Gateway
from vl_core.a2a import (
    GatewayClient,
    GatewayVerifier,
    a2a_router,
    register_credential,
    set_transport,
)
from vl_core.crypto import LocalSigner
from vl_core.schemas import (
    A2AEnvelope,
    CredentialRequest,
    Decision,
    DecisionStatus,
    OrderLine,
    OrderProposal,
)
from vl_core.settings import Settings

GW = "http://gateway.test"
VENDOR = "http://vendor.test"
RETAILER = "http://retailer.test"
ADMIN = "test-admin"
MAYA = "vendor-agent:maya"
FRESHCART = "retailer-agent:freshcart"


def order(**changes: object) -> OrderProposal:
    base = OrderProposal(
        order_id="ord-1",
        version=1,
        retailer_id="retailer:freshcart",
        vendor_id="vendor:maya",
        store_id="store:hub-4",
        lines=(OrderLine(sku="spinach", qty=Decimal(350), unit="bunch", price=Decimal(40)),),
        pickup_at=datetime(2026, 10, 15, 0, 0, tzinfo=UTC),
        payment_days=7,
        currency="INR",
        terms_version="qc-standard-v1",
    )
    return base.model_copy(update=changes)


@dataclass
class Env:
    gateway: Gateway
    vendor: GatewayClient
    retailer: GatewayClient
    signer: LocalSigner
    received: list[A2AEnvelope]


def target_app(received: list[A2AEnvelope]) -> FastAPI:
    app = FastAPI()

    async def on_propose(env: A2AEnvelope) -> Decision:
        received.append(env)
        proposal = OrderProposal.model_validate(env.body)
        return Decision(
            order_id=proposal.order_id, version=proposal.version, status=DecisionStatus.GREEN
        )

    async def owns(agent_id: str) -> bool:
        return agent_id == MAYA

    app.include_router(a2a_router(GatewayVerifier(GW), {"order.propose": on_propose}, owns))
    return app


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    settings = Settings(
        vl_admin_token=ADMIN, vl_dev_endpoints=True, vl_operator_token="test-operator"
    )
    gw_app = create_app(settings, LocalSigner())
    received: list[A2AEnvelope] = []
    set_transport(AppRouter({GW: gw_app, VENDOR: target_app(received), RETAILER: FastAPI()}))
    signer = LocalSigner()
    vendor = GatewayClient(GW, MAYA, signer)
    retailer = GatewayClient(GW, FRESHCART, signer)
    for client, party, obo, endpoint in (
        (vendor, "vendor", "vendor:maya", VENDOR),
        (retailer, "retailer", "retailer:freshcart", RETAILER),
    ):
        await register_credential(
            GW,
            ADMIN,
            CredentialRequest(
                agent_id=client.agent_id,
                party_type=party,
                on_behalf_of=obo,  # type: ignore[arg-type]
                public_key=await client.public_key_b64(),
                endpoint=endpoint,
            ),
        )
    yield Env(gw_app.state.gateway, vendor, retailer, signer, received)
    set_transport(None)
