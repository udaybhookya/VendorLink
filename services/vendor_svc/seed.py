"""Seed the vendor tenant from seed/vendors.json through the real flows (incl. rules consent)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from vendor_svc.operations import VendorOperations
from vl_core.crypto import hash_obj
from vl_core.schemas import ConsentRequest, Listing, RulesDraft, Vendor

SEED = Path(__file__).resolve().parents[2] / "seed" / "vendors.json"


class _Consent(BaseModel):
    transcript: str
    language: str


class _SeedVendor(BaseModel):
    vendor: Vendor
    rules: RulesDraft | None = None
    listings: list[Listing] = []  # her copy of listings the retailer has published
    consent: _Consent | None = None


def load_seed(path: Path = SEED) -> list[_SeedVendor]:
    return [_SeedVendor.model_validate(v) for v in json.loads(path.read_text())["vendors"]]


async def seed_vendors(services: VendorOperations, path: Path = SEED) -> list[str]:
    entries = load_seed(path)
    services.control.reset()  # the gateway was just reset; register host + agents again
    for entry in entries:
        await services.register_vendor(entry.vendor)
        for listing in entry.listings:
            await services.store.listings.put(listing.id, listing)
        if entry.rules is not None and entry.consent is not None:
            draft, _ = await services.draft_rules(entry.vendor.id, entry.rules)
            await services.consent_to_rules(
                entry.vendor.id,
                ConsentRequest(
                    subject_hash=hash_obj(draft.unsigned()),
                    transcript=entry.consent.transcript,
                    language=entry.consent.language,
                ),
            )
    await services.control.ensure_registered()
    return [e.vendor.id for e in entries]
