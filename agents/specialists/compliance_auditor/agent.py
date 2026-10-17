"""compliance.check: vendor documents + listings against the chain's supplier standards."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agents.base import Agent, skill
from vl_core.schemas import DocCheck, ListingFlag, VendorCheckIn, VendorCheckOut


class ComplianceAuditor(Agent):
    class Options(BaseModel):
        blocking_listing_flags: list[str] = Field(
            default=["MISSING_REGISTRATION_", "UNSUPPORTED_CLAIM_", "HALAL_UNVERIFIED"],
            description="Listing flag codes (or prefixes) that keep a supplier from passing",
        )

    @skill("compliance.check")
    async def audit(self, inp: VendorCheckIn) -> VendorCheckOut:
        specs = [*inp.country_pack.id_docs, *inp.country_pack.food_safety_regs]
        checks: list[DocCheck] = []
        for role in inp.business_profile.required_docs:
            options = [s for s in specs if s.role == role]
            given = [(s, inp.doc_numbers[s.id]) for s in options if s.id in inp.doc_numbers]
            if not given:
                checks.append(
                    DocCheck(
                        doc_type=f"role:{role}",
                        status="fail",
                        code="MISSING_DOCUMENT",
                        detail=" or ".join(s.id for s in options),
                    )
                )
            elif not any(re.fullmatch(s.regex, n.strip()) for s, n in given):
                checks.append(DocCheck(doc_type=f"role:{role}", status="fail", code="BAD_FORMAT"))
            else:
                checks.append(DocCheck(doc_type=f"role:{role}", status="pass", code="OK"))
        flags = [
            ListingFlag(code="LISTING_NOT_COMPLIANT", detail=f"{listing.id}: {flag.code}")
            for listing in inp.listings
            for flag in listing.flags
            if flag.code.startswith(tuple(self.options.blocking_listing_flags))
        ]
        passed = all(c.status == "pass" for c in checks) and not flags
        return VendorCheckOut(checks=checks, flags=flags, passed=passed)
