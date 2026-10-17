"""catalogue.build: photo + the vendor's words → Listing draft in the retailer's terms."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

from agents.base import Agent, skill
from agents.specialists.catalogue_builder.prompts import BUILD_LISTING
from vl_core.llm import ImageInput
from vl_core.schemas import (
    CatalogueDraft,
    InfoRequest,
    Listing,
    ListingFlag,
    ListingStepIn,
    ListingStepOut,
)

PATTERN = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>[^\W\d_]+)\s+(?:of\s+)?(?P<sku>[^\W\d_][^\d@]*?)\s+"
    r"(?:at|@|for)\s*(?:rs\.?|₹|inr|rp\.?|idr)?\s*(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def claim_flags(
    claims: list[str], transcript: str, requiring_proof: list[str]
) -> list[ListingFlag]:
    text = " ".join([transcript, *claims]).casefold()
    return [
        ListingFlag(
            code="UNSUPPORTED_CLAIM_" + re.sub(r"\W+", "_", claim).upper().strip("_"),
            detail=f"'{claim}' needs proof before it can be shown",
        )
        for claim in requiring_proof
        if claim.casefold() in text
    ]


def parse_without_model(transcript: str) -> CatalogueDraft | None:
    """Explicit, flagged fallback: '<qty> <unit> [of] <sku> at <price>'."""
    match = PATTERN.search(transcript)
    if match is None:
        return None
    return CatalogueDraft(
        sku_name=match["sku"].strip().lower(),
        category="",
        unit=match["unit"].lower(),
        price=match["price"],
        qty=match["qty"],
        confidence=0.5,
    )


def _dec(text: str | None) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


class CatalogueBuilder(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"
        parse_without_model: bool = True

    @skill("catalogue.build")
    async def build(self, inp: ListingStepIn) -> ListingStepOut:
        images = []
        if inp.photo_uri:
            images.append(ImageInput(await self.ctx.blob().get(inp.photo_uri)))
        categories = inp.retailer_profile.categories
        units = sorted({*inp.business_profile.units, *(u for c in categories for u in c.units)})
        prompt = BUILD_LISTING.substitute(
            language=inp.language,
            categories=", ".join(c.id for c in categories),
            units=", ".join(units),
            transcript=inp.transcript.replace('"""', "'''"),
        )
        flags: list[ListingFlag] = []
        result = await self.ctx.llm(self.options.llm_tier).generate_json(
            prompt, CatalogueDraft, images
        )
        draft = result.value
        if draft is None:
            text = " ".join([inp.transcript, inp.answers.get("describe", "")]).strip()
            parsed = parse_without_model(text) if self.options.parse_without_model else None
            if parsed is None:
                ask = InfoRequest(
                    field="describe",
                    ask="vendor",
                    answer_type="text",
                    question_key="hitl.q.describe",
                )
                return ListingStepOut(
                    listing=None, needs_review=True, notes=result.error or "", requests=[ask]
                )
            draft = parsed
            flags.append(ListingFlag(code="PARSED_WITHOUT_MODEL", detail=result.error or ""))

        category = next((c for c in categories if c.id == draft.category), None)
        if category is None:
            category = next((c for c in categories if draft.unit in c.units), None)
            flags.append(ListingFlag(code="CATEGORY_GUESSED", detail=draft.category))
        if category is None or draft.unit not in category.units:
            flags.append(ListingFlag(code="UNIT_NOT_ACCEPTED", detail=draft.unit))
        price = _dec(draft.price) or _dec(inp.answers.get("price"))
        qty = _dec(draft.qty) or _dec(inp.answers.get("qty"))
        requests = []
        if price is None or price < 0:
            requests.append(
                InfoRequest(
                    field="price",
                    ask="vendor",
                    answer_type="number",
                    question_key="hitl.q.price",
                    params={"sku": draft.sku_name, "unit": draft.unit},
                )
            )
        if qty is None or qty <= 0:
            requests.append(
                InfoRequest(
                    field="qty",
                    ask="vendor",
                    answer_type="number",
                    question_key="hitl.q.qty",
                    params={"sku": draft.sku_name, "unit": draft.unit},
                )
            )
        if requests:
            flags.append(ListingFlag(code="PRICE_OR_QTY_MISSING"))
        flags += claim_flags(
            draft.claims, inp.transcript, inp.retailer_profile.claims_requiring_proof
        )

        listing = Listing(
            id=f"lst-{uuid.uuid4().hex[:10]}",
            vendor_id=inp.vendor_id,
            sku_name=draft.sku_name,
            category=category.id if category else "uncategorised",
            unit=draft.unit,
            price=price if price is not None and price >= 0 else Decimal(0),
            currency=inp.country_pack.currency,
            qty=qty if qty is not None and qty > 0 else Decimal(1),
            photos=[inp.photo_uri] if inp.photo_uri else [],
            flags=flags,
            confidence=draft.confidence,
            status="draft",
        )
        return ListingStepOut(
            listing=listing, flags=flags, needs_review=bool(flags), requests=requests
        )
