"""May her agent approve an improved listing on her behalf? Deterministic, like every Guardian
decision: it compares the listing with her own words and her listing rules. No model.

Reason codes (why she must look first):
- PRICE_NOT_WHAT_SHE_SAID / QTY_NOT_WHAT_SHE_SAID: the number is not one she said or answered.
- FACTS_NOT_FROM_HER: an attribute or claim came only from the model.
- TEXT_REWRITE_NEEDS_HER_OK: her rules say she reads rewritten copy herself.
- AI_PHOTO_NEEDS_HER_OK: an AI-enhanced photo exists and her rules say she picks photos herself.
- PHOTO_BELOW_STANDARD: the photo misses the retailer's standards and nothing can fix it here
  (she can retake it, or send it as it is).
"""

from __future__ import annotations

from vl_core.facts import said_number
from vl_core.schemas import ListingApprovalIn, ListingApprovalOut

MODEL_ONLY = "FACT_NOT_FROM_VENDOR_"
PHOTO = "IMAGE_"


def review_listing(inp: ListingApprovalIn) -> ListingApprovalOut:
    listing, rules = inp.listing, inp.rules
    words = [inp.transcript, *inp.answers.values()]
    codes = {f.code for f in listing.flags}
    reasons: list[str] = []
    if rules.price_must_match:
        if not said_number(listing.price, *words):
            reasons.append("PRICE_NOT_WHAT_SHE_SAID")
        if not said_number(listing.qty, *words):
            reasons.append("QTY_NOT_WHAT_SHE_SAID")
    if any(s == "model" for k, s in listing.sources.items() if k not in ("price", "qty")) or any(
        c.startswith(MODEL_ONLY) for c in codes
    ):
        reasons.append("FACTS_NOT_FROM_HER")
    if not rules.allow_text_rewrite and (listing.title or listing.description):
        reasons.append("TEXT_REWRITE_NEEDS_HER_OK")
    rendered = [c for c in listing.images if c.kind == "generated" and c.uri]
    image = "original"
    if rendered:
        if rules.allow_ai_images:
            image = rendered[0].id
        else:
            reasons.append("AI_PHOTO_NEEDS_HER_OK")
    elif any(c.startswith(PHOTO) for c in codes):
        reasons.append("PHOTO_BELOW_STANDARD")
    return ListingApprovalOut(auto_approve=not reasons, image=image, reasons=reasons)
