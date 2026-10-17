"""Seed the retailer tenant: retailer, agent identity, catalogue, approved vendors, config."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from retailer_svc.operations import RetailerOperations
from vl_core.a2a import agent_id_for, register_credential
from vl_core.schemas import CredentialRequest, Listing, Retailer, RetailerVendor

SEED = Path(__file__).resolve().parents[2] / "seed"


class _SeedRetailer(BaseModel):
    retailer: Retailer
    approved_vendors: list[str]
    listings: list[Listing]


def load_seed(path: Path = SEED / "retailer.json") -> _SeedRetailer:
    return _SeedRetailer.model_validate(json.loads(path.read_text()))


def _vendor_profiles(path: Path = SEED / "vendors.json") -> dict[str, dict[str, str]]:
    return {v["vendor"]["id"]: v["vendor"] for v in json.loads(path.read_text())["vendors"]}


async def seed_retailer(orch: RetailerOperations, preset: str) -> str:
    data = load_seed()
    orch.control.reset()  # the gateway was just reset; register host + agents again
    retailer = data.retailer
    await orch.store.retailers.put(retailer.id, retailer)
    await register_credential(
        orch.settings.trust_gateway_url,
        orch.settings.vl_admin_token,
        CredentialRequest(
            agent_id=agent_id_for(retailer.id),
            party_type="retailer",
            on_behalf_of=retailer.id,
            display_name=retailer.display_name,
            public_key=await orch.gateway(retailer.id).public_key_b64(),
            endpoint=orch.settings.retailer_svc_url,
        ),
    )
    profiles = _vendor_profiles()
    for vendor_id in data.approved_vendors:
        v = profiles[vendor_id]
        await orch.store.vendors.put(
            vendor_id,
            RetailerVendor(
                vendor_id=vendor_id,
                display_name=v["display_name"],
                country=v["country"],
                business_profile=v["business_profile"],
                status="approved",
                updated_at=datetime.now(UTC),
            ),
        )
    for listing in data.listings:
        await orch.store.listings.put(listing.id, listing)
    await orch.activate(retailer.id, orch.studio.loader.load_preset(preset).refs, preset)
    await orch.control.ensure_registered()
    return retailer.id
