"""grade.label: label → fields; deterministic checks against the Country Pack formats."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from agents.base import Agent, skill
from agents.specialists.label_checker.prompts import READ_LABEL
from vl_core.llm import ImageInput
from vl_core.schemas import (
    FALLBACK_LANGUAGE,
    CountryPack,
    DocSpec,
    LabelFields,
    ListingFlag,
    ListingStepIn,
    ListingStepOut,
)

FIELDS = ("ingredients", "allergens", "expiry_date", "net_weight")


def unanchored(regex: str) -> re.Pattern[str]:
    """Country Pack regexes are anchored for whole values; find them inside label text."""
    core = regex.removeprefix("^").removesuffix("$")
    return re.compile(rf"(?<![\w-])(?:{core})(?![\w-])")


def fields_from_text(text: str, country: CountryPack) -> LabelFields:
    """Deterministic read of already-extracted label text (no model needed)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        lowered = line.casefold()
        for field, words in country.label_keywords.items():
            for word in words:
                at = lowered.find(word.casefold())
                if field not in values and at >= 0:
                    values[field] = line[at + len(word) :].strip(" :.-") or line.strip()
    numbers = {
        spec.id: m.group(0)
        for spec in country.food_safety_regs
        if (m := unanchored(spec.regex).search(text)) is not None
    }
    return LabelFields(
        **{k: v for k, v in values.items() if k in FIELDS},
        registration_numbers=numbers,
        halal_logo=any(
            word.casefold() in text.casefold() for word in country.label_keywords.get("halal", [])
        ),
        confidence=0.9,
    )


def registration_found(spec: DocSpec, fields: LabelFields, text: str) -> bool:
    pattern = unanchored(spec.regex)
    candidates = [*fields.registration_numbers.values(), text]
    return any(pattern.search(c.replace(" ", "")) or pattern.search(c) for c in candidates)


class LabelChecker(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"

    @skill("grade.label")
    async def check(self, inp: ListingStepIn) -> ListingStepOut:
        listing = inp.listing
        text = inp.label_text or ""
        fields: LabelFields | None = None
        error = ""
        if inp.photo_uri:
            photo = ImageInput(await self.ctx.blob().get(inp.photo_uri))
            result = await self.ctx.llm(self.options.llm_tier).generate_json(
                READ_LABEL.substitute(), LabelFields, [photo]
            )
            fields, error = result.value, result.error or ""
        if fields is None and text:
            fields = fields_from_text(text, inp.country_pack)
        if fields is None:
            flag = ListingFlag(code="LABEL_NEEDS_REVIEW", detail=error or "no label photo or text")
            out = listing.model_copy(update={"flags": [*listing.flags, flag]}) if listing else None
            return ListingStepOut(listing=out, flags=[flag], needs_review=True)

        flags: list[ListingFlag] = []
        required = inp.business_profile.grading_rules.params.get("label_required_fields", [])
        for field in required:
            if field in FIELDS and not getattr(fields, field):
                flags.append(ListingFlag(code=f"MISSING_LABEL_FIELD_{field.upper()}"))
        if "food_safety_registration" in required:
            for spec in inp.country_pack.food_safety_regs:
                if spec.role == "food_safety_registration" and not registration_found(
                    spec, fields, text
                ):
                    flags.append(
                        ListingFlag(
                            code=f"MISSING_REGISTRATION_{spec.id.upper()}",
                            detail=f"no {spec.name.get(FALLBACK_LANGUAGE, spec.id)} number "
                            "on the label",
                        )
                    )
        halal_specs = [
            s for s in inp.country_pack.food_safety_regs if s.role == "halal_certificate"
        ]
        if (
            fields.halal_logo
            and halal_specs
            and not any(registration_found(s, fields, text) for s in halal_specs)
        ):
            flags.append(
                ListingFlag(code="HALAL_UNVERIFIED", detail="halal shown without certificate")
            )

        if listing is None:
            return ListingStepOut(listing=None, flags=flags, needs_review=True)
        attributes = {
            **listing.attributes,
            **{f: v for f in FIELDS if (v := getattr(fields, f))},
            "halal": fields.halal_logo,
        }
        updated = listing.model_copy(
            update={
                "attributes": attributes,
                "flags": [*listing.flags, *flags],
                "confidence": min(listing.confidence, fields.confidence),
            }
        )
        return ListingStepOut(listing=updated, flags=flags, needs_review=bool(flags))
