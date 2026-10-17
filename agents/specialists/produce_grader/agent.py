"""grade.produce: photo → grade, freshness, shelf life, confidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agents.base import Agent, skill
from agents.specialists.produce_grader.prompts import GRADE_PHOTO
from vl_core.llm import ImageInput
from vl_core.schemas import GradeResult, ListingFlag, ListingStepIn, ListingStepOut


class ProduceGrader(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"

    @skill("grade.produce")
    async def grade(self, inp: ListingStepIn) -> ListingStepOut:
        if inp.listing is None:
            return ListingStepOut(listing=None, needs_review=True, notes="nothing to grade")
        listing = inp.listing
        if not inp.photo_uri:
            flag = ListingFlag(code="NO_PHOTO", detail="produce needs a photo to be graded")
            return ListingStepOut(
                listing=listing.model_copy(update={"flags": [*listing.flags, flag]}),
                flags=[flag],
                needs_review=True,
            )
        scale = inp.business_profile.grading_rules.scale
        prompt = GRADE_PHOTO.substitute(scale=", ".join(scale))
        photo = ImageInput(await self.ctx.blob().get(inp.photo_uri))
        result = await self.ctx.llm(self.options.llm_tier).generate_json(
            prompt, GradeResult, [photo]
        )
        if result.value is None:
            flag = ListingFlag(code="GRADE_NEEDS_REVIEW", detail=result.error or "")
            return ListingStepOut(
                listing=listing.model_copy(update={"flags": [*listing.flags, flag]}),
                flags=[flag],
                needs_review=True,
            )
        graded = result.value
        flags: list[ListingFlag] = []
        if graded.grade not in scale:
            flags.append(ListingFlag(code="GRADE_NOT_ON_SCALE", detail=graded.grade))
        min_grade = inp.retailer_profile.quality_thresholds.min_grade
        if (
            min_grade in scale
            and graded.grade in scale
            and scale.index(graded.grade) > scale.index(min_grade)
        ):
            flags.append(
                ListingFlag(code="BELOW_MIN_GRADE", detail=f"{graded.grade} < {min_grade}")
            )
        min_life = inp.retailer_profile.quality_thresholds.min_shelf_life_days
        if min_life is not None and graded.shelf_life_days < min_life:
            flags.append(
                ListingFlag(code="SHELF_LIFE_TOO_SHORT", detail=str(graded.shelf_life_days))
            )
        updated = listing.model_copy(
            update={
                "grade": graded.grade,
                "shelf_life_days": graded.shelf_life_days,
                "attributes": {**listing.attributes, "freshness": graded.freshness},
                "confidence": min(listing.confidence, graded.confidence),
                "flags": [*listing.flags, *flags],
            }
        )
        return ListingStepOut(listing=updated, flags=flags, needs_review=bool(flags))
