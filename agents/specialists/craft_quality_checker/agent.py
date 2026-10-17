"""grade.craft: photo of a handmade product → grade, finish, defects, batch consistency, confidence.

The model only describes what it sees. Every flag is decided here, deterministically, from the
Business Profile's quality rules and the Retailer Profile's thresholds and claim list.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from agents.base import Agent, skill
from agents.specialists.craft_quality_checker.prompts import CHECK_PHOTO
from vl_core.llm import ImageInput
from vl_core.schemas import Listing, ListingFlag, ListingStepIn, ListingStepOut
from vl_core.schemas.common import Fraction


class CraftResult(BaseModel):
    """What the vision model returns. Internal to this agent: it never crosses a boundary."""

    is_product: bool = Field(description="False if the photo shows no handmade product")
    grade: str
    finish: Literal["clean", "minor_flaws", "rough"]
    defects: list[str] = Field(default_factory=list, description="Names from the defect list")
    batch_consistency: Literal["single_item", "consistent", "minor_variation", "inconsistent"]
    visible_claims: list[str] = Field(
        default_factory=list, description="Claims printed on labels, tags or packaging"
    )
    confidence: Fraction


def code(text: str) -> str:
    return re.sub(r"\W+", "_", text).upper().strip("_")


def claim_flags(texts: list[str], requiring_proof: list[str]) -> list[ListingFlag]:
    """Claims from the retailer's list found in the vendor's words or on the product itself."""
    text = " ".join(texts).casefold()
    return [
        ListingFlag(
            code="UNSUPPORTED_CLAIM_" + code(claim),
            detail=f"'{claim}' needs proof before it can be shown",
        )
        for claim in requiring_proof
        if claim.casefold() in text
    ]


def judge(result: CraftResult, inp: ListingStepIn) -> list[ListingFlag]:
    """Flags from the model's description; rules come only from config."""
    scale = inp.business_profile.grading_rules.scale
    params = inp.business_profile.grading_rules.params
    flags: list[ListingFlag] = []
    if not result.is_product:
        flags.append(ListingFlag(code="NOT_A_PRODUCT_PHOTO"))
    if result.grade not in scale:
        flags.append(ListingFlag(code="GRADE_NOT_ON_SCALE", detail=result.grade))
    min_grade = inp.retailer_profile.quality_thresholds.min_grade
    if (
        min_grade in scale
        and result.grade in scale
        and scale.index(result.grade) > scale.index(min_grade)
    ):
        flags.append(ListingFlag(code="BELOW_MIN_GRADE", detail=f"{result.grade} < {min_grade}"))
    blocking = {d.casefold() for d in params.get("blocking_defects", [])}
    flags += [
        ListingFlag(code="DEFECT_" + code(d), detail=d)
        for d in dict.fromkeys(result.defects)
        if d.casefold() in blocking
    ]
    if params.get("require_batch_consistency") and result.batch_consistency == "inconsistent":
        flags.append(ListingFlag(code="BATCH_INCONSISTENT"))
    return flags


class CraftQualityChecker(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"

    @skill("grade.craft")
    async def check(self, inp: ListingStepIn) -> ListingStepOut:
        if inp.listing is None:
            return ListingStepOut(listing=None, needs_review=True, notes="nothing to check")
        listing = inp.listing
        seen = {f.code for f in listing.flags}
        claims = claim_flags([inp.transcript], inp.retailer_profile.claims_requiring_proof)
        if not inp.photo_uri:
            flags = [
                ListingFlag(code="NO_PHOTO", detail="a handmade product needs a photo"),
                *claims,
            ]
            return self._out(listing, [f for f in flags if f.code not in seen])
        params = inp.business_profile.grading_rules.params
        prompt = CHECK_PHOTO.substitute(
            scale=", ".join(inp.business_profile.grading_rules.scale),
            grade_guide=params.get("grade_guide", ""),
            defects=", ".join(params.get("defects", [])) or "any visible flaw",
            claims=", ".join(inp.retailer_profile.claims_requiring_proof) or "none",
        )
        photo = ImageInput(await self.ctx.blob().get(inp.photo_uri))
        result = await self.ctx.llm(self.options.llm_tier).generate_json(
            prompt, CraftResult, [photo]
        )
        if result.value is None:
            flags = [ListingFlag(code="CRAFT_NEEDS_REVIEW", detail=result.error or ""), *claims]
            return self._out(listing, [f for f in flags if f.code not in seen])
        found = result.value
        claims = claim_flags(
            [inp.transcript, *found.visible_claims], inp.retailer_profile.claims_requiring_proof
        )
        flags = [f for f in [*judge(found, inp), *claims] if f.code not in seen]
        updated = listing.model_copy(
            update={
                "grade": found.grade,
                "attributes": {
                    **listing.attributes,
                    "finish": found.finish,
                    "batch_consistency": found.batch_consistency,
                    "defects": ", ".join(found.defects),
                },
                "confidence": min(listing.confidence, found.confidence),
            }
        )
        return self._out(updated, flags)

    @staticmethod
    def _out(listing: Listing, flags: list[ListingFlag]) -> ListingStepOut:
        updated = listing.model_copy(update={"flags": [*listing.flags, *flags]})
        return ListingStepOut(listing=updated, flags=flags, needs_review=bool(flags))
