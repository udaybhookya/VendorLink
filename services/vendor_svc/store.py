from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from vl_core.schemas import (
    ConsentRecord,
    Deal,
    Listing,
    StandingMandate,
    Vendor,
    VendorInvite,
    VendorTerms,
)
from vl_core.settings import Settings
from vl_core.store import Repo, get_repo

PREFIX = "vendor."


@dataclass
class VendorStore:
    """Vendor-tenant state owned by vendor-svc. Mandates and commitments belong to the Guardian."""

    vendors: Repo[Vendor]
    rule_drafts: Repo[StandingMandate]
    deals: Repo[Deal]
    consents: Repo[ConsentRecord]
    listings: Repo[Listing]
    invites: Repo[VendorInvite]
    terms: Repo[VendorTerms]

    @classmethod
    def create(cls, settings: Settings) -> VendorStore:
        def repo[T: BaseModel](model: type[T], name: str) -> Repo[T]:
            return get_repo(model, f"{PREFIX}{name}", settings)

        return cls(
            vendors=repo(Vendor, "vendors"),
            rule_drafts=repo(StandingMandate, "rule_drafts"),
            deals=repo(Deal, "deals"),
            consents=repo(ConsentRecord, "consents"),
            listings=repo(Listing, "listings"),
            invites=repo(VendorInvite, "invites"),
            terms=repo(VendorTerms, "terms"),
        )
