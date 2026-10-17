"""listing.enrich: her facts → catalogue title, description, bullet points and attributes.

A model writes the copy. Code checks it: a fact the model says came from her must appear in her
words, or it is recorded as the model's; a claim from the retailer's proof list that she never
made is flagged; required attributes nobody gave are flagged; title and bullet limits come from
the Retailer Profile's `catalogue_standards`. Her agent later approves only facts that are hers.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from agents.base import Agent, skill
from agents.specialists.listing_enricher.prompts import ENRICH
from vl_core.facts import mentions, said_number
from vl_core.llm import ImageInput
from vl_core.schemas import (
    AttributeSpec,
    FactSource,
    ListingFlag,
    ListingStepIn,
    ListingStepOut,
    TextStandards,
)


class Fact(BaseModel):
    key: str
    value: str
    source: Literal["vendor", "photo", "model"]
    evidence: str = ""


class Enrichment(BaseModel):
    """What the model returns. Internal to this agent: it never crosses a boundary."""

    title: str
    description: str
    bullets: list[str] = Field(default_factory=list)
    attributes: list[Fact] = Field(default_factory=list)


def code(text: str) -> str:
    return re.sub(r"\W+", "_", text).upper().strip("_")


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-– ") or text[:limit]


def typed(spec: AttributeSpec, value: str) -> str | int | float | bool | None:
    """The value in the attribute's type, or None if it does not fit."""
    value = value.strip()
    try:
        if spec.type == "int":
            return int(Decimal(re.sub(r"[^\d.]", "", value)))
        if spec.type == "decimal":
            return float(Decimal(re.sub(r"[^\d.]", "", value)))
    except (InvalidOperation, ValueError):
        return None
    if spec.type == "bool":
        return value.casefold() in ("true", "yes", "1")
    if spec.type == "enum":
        match = next((o for o in spec.options if o.casefold() == value.casefold()), None)
        return match
    return value or None


def source_of(fact: Fact, words: list[str], has_photo: bool) -> FactSource:
    """Trust the model's 'she said it' only when her words show it: its quoted evidence, or the
    value itself for text. A bare number is not enough ("40 candles" is not "40 hours")."""
    numeric = bool(re.fullmatch(r"[\d.,\s]+[a-zA-Z%]{0,3}", fact.value.strip()))
    if fact.source == "vendor" and (
        (fact.evidence and mentions(fact.evidence, *words))
        or (not numeric and mentions(fact.value, *words))
    ):
        return "vendor"
    if fact.source == "photo" and has_photo:
        return "photo"
    return "model"


class ListingEnricher(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"

    @skill("listing.enrich")
    async def enrich(self, inp: ListingStepIn) -> ListingStepOut:
        listing = inp.listing
        if listing is None:
            return ListingStepOut(listing=None)
        standards = inp.retailer_profile.catalogue_standards
        text_std = standards.text if standards else TextStandards()
        answers = list(inp.answers.values())
        sources: dict[str, FactSource] = {
            **listing.sources,
            "price": self._number_source(listing.price, inp.transcript, answers),
            "qty": self._number_source(listing.qty, inp.transcript, answers),
        }
        specs = {a.key: a for a in inp.business_profile.attributes}
        category = next(
            (c for c in inp.retailer_profile.categories if c.id == listing.category), None
        )
        prompt = ENRICH.substitute(
            sku=listing.sku_name,
            category=category.name.get("en", category.id) if category else listing.category,
            qty=listing.qty,
            unit=listing.unit,
            currency=listing.currency,
            price=listing.price,
            transcript=inp.transcript.replace('"""', "'''"),
            answers=json.dumps(inp.answers, ensure_ascii=False) if inp.answers else "none",
            attributes="; ".join(
                f"{a.key}: {a.type}" + (f" ({', '.join(a.options)})" if a.options else "")
                for a in specs.values()
            )
            or "none",
            title_max=text_std.title_max_chars,
            min_words=text_std.description_min_words,
            claims=", ".join(inp.retailer_profile.claims_requiring_proof) or "none",
            bullets=text_std.bullets,
        )
        images = []
        if inp.photo_uri:
            images.append(ImageInput(await self.ctx.blob().get(inp.photo_uri)))
        result = await self.ctx.llm(self.options.llm_tier).generate_json(prompt, Enrichment, images)
        if result.value is None:
            flag = ListingFlag(code="TEXT_NEEDS_REVIEW", detail=result.error or "")
            updated = listing.model_copy(
                update={"sources": sources, "flags": [*listing.flags, flag]}
            )
            return ListingStepOut(listing=updated, flags=[flag], needs_review=True)
        found = result.value
        words = [inp.transcript, *answers]
        flags: list[ListingFlag] = []
        attributes = dict(listing.attributes)
        for fact in found.attributes:
            spec = specs.get(fact.key)
            value = typed(spec, fact.value) if spec else None
            if spec is None or value is None:
                continue  # not an attribute this business has, or the wrong type
            if fact.key in inp.answers:
                attributes[fact.key], sources[fact.key] = (
                    typed(spec, inp.answers[fact.key]) or value,
                    "answer",
                )
                continue
            source = source_of(fact, words, bool(inp.photo_uri))
            if source == "model" and not spec.required:
                continue  # a guess for an optional attribute is left out, not shown to buyers
            attributes[fact.key], sources[fact.key] = value, source
            if source == "model":
                flags.append(
                    ListingFlag(code=f"FACT_NOT_FROM_VENDOR_{code(fact.key)}", detail=fact.value)
                )
        flags += [
            ListingFlag(code=f"ATTRIBUTE_MISSING_{code(key)}")
            for key, spec in specs.items()
            if spec.required and key not in attributes
        ]
        title = shorten(found.title, text_std.title_max_chars)
        bullets = [shorten(b, 120) for b in found.bullets[: text_std.bullets]]
        copy = [title, found.description, *bullets]
        flags += [
            ListingFlag(
                code=f"FACT_NOT_FROM_VENDOR_CLAIM_{code(claim)}",
                detail=f"the text says '{claim}' but she did not",
            )
            for claim in inp.retailer_profile.claims_requiring_proof
            if mentions(claim, *copy) and not mentions(claim, *words)
        ]
        if len(found.description.split()) < text_std.description_min_words:
            flags.append(ListingFlag(code="TEXT_DESCRIPTION_SHORT"))
        seen = {f.code for f in listing.flags}
        flags = [f for f in flags if f.code not in seen]
        updated = listing.model_copy(
            update={
                "title": title,
                "description": " ".join(found.description.split()),
                "bullets": bullets,
                "attributes": attributes,
                "sources": sources,
                "flags": [*listing.flags, *flags],
            }
        )
        self.ctx.log().info(
            "listing enriched",
            attributes=len(found.attributes),
            from_her=sum(1 for s in sources.values() if s in ("vendor", "answer")),
            from_model=sum(1 for s in sources.values() if s == "model"),
            flags=",".join(f.code for f in flags) or None,
        )
        return ListingStepOut(listing=updated, flags=flags, needs_review=bool(flags))

    @staticmethod
    def _number_source(value: Decimal, transcript: str, answers: list[str]) -> FactSource:
        if said_number(value, transcript):
            return "vendor"
        if said_number(value, *answers):
            return "answer"
        return "model"
