"""guardian.review_listing: approve an improved listing within her rules, or say why she must look.
Deterministic: every case is decided from her words, her rules and the listing."""

from __future__ import annotations

from decimal import Decimal

from agents.core.guardian.listing import review_listing
from vl_core.schemas import (
    ImageCandidate,
    Listing,
    ListingApprovalIn,
    ListingFlag,
    ListingRules,
)

SAID = "40 lavender candles at 220 rupees, soy wax, 200 grams"


def listing(**changes: object) -> Listing:
    base = Listing(
        id="l1",
        vendor_id="vendor:maya",
        sku_name="lavender candle",
        category="candles",
        unit="piece",
        price=Decimal(220),
        currency="INR",
        qty=Decimal(40),
        title="Hand-poured lavender candle",
        description="A calm lavender candle.",
        sources={"price": "vendor", "qty": "vendor", "wax_type": "vendor"},
        images=[ImageCandidate(id="original", kind="original", uri="blob://p.jpg")],
        photos=["blob://p.jpg"],
    )
    return base.model_copy(update=changes)


def decide(
    item: Listing, rules: ListingRules | None = None, said: str = SAID
) -> tuple[bool, list[str], str]:
    out = review_listing(
        ListingApprovalIn(listing=item, transcript=said, rules=rules or ListingRules())
    )
    return out.auto_approve, out.reasons, out.image


def test_her_own_facts_are_approved_for_her() -> None:
    assert decide(listing()) == (True, [], "original")


def test_a_price_she_never_said_needs_her() -> None:
    assert "PRICE_NOT_WHAT_SHE_SAID" in decide(listing(price=Decimal(250)))[1]


def test_a_quantity_she_never_said_needs_her() -> None:
    assert "QTY_NOT_WHAT_SHE_SAID" in decide(listing(qty=Decimal(50)))[1]


def test_an_answer_counts_as_her_words() -> None:
    out = review_listing(
        ListingApprovalIn(
            listing=listing(price=Decimal(250)), transcript="40 candles", answers={"price": "250"}
        )
    )
    assert out.auto_approve


def test_a_fact_only_the_model_gave_needs_her() -> None:
    item = listing(sources={"price": "vendor", "qty": "vendor", "burn_time_hours": "model"})
    assert decide(item)[1] == ["FACTS_NOT_FROM_HER"]
    flagged = listing(flags=[ListingFlag(code="FACT_NOT_FROM_VENDOR_CLAIM_NON_TOXIC")])
    assert decide(flagged)[1] == ["FACTS_NOT_FROM_HER"]


def test_a_rewrite_needs_her_when_her_rules_say_so() -> None:
    assert decide(listing(), ListingRules(allow_text_rewrite=False))[1] == [
        "TEXT_REWRITE_NEEDS_HER_OK"
    ]


def test_an_ai_photo_needs_her_unless_her_rules_allow_it() -> None:
    ai = listing(
        images=[
            ImageCandidate(id="original", kind="original", uri="blob://p.jpg"),
            ImageCandidate(id="img-1", kind="generated", uri="blob://p-1.jpg", prompt="…"),
        ],
        flags=[ListingFlag(code="IMAGE_CLUTTERED_BACKGROUND")],
    )
    assert decide(ai) == (False, ["AI_PHOTO_NEEDS_HER_OK"], "original")
    assert decide(ai, ListingRules(allow_ai_images=True)) == (True, [], "img-1")


def test_a_prompt_without_a_rendered_photo_is_not_a_photo() -> None:
    tips_only = listing(
        images=[
            ImageCandidate(id="original", kind="original", uri="blob://p.jpg"),
            ImageCandidate(id="img-1", kind="generated", uri=None, prompt="…"),
        ],
        flags=[ListingFlag(code="IMAGE_BLURRY")],
    )
    assert decide(tips_only, ListingRules(allow_ai_images=True)) == (
        False,
        ["PHOTO_BELOW_STANDARD"],
        "original",
    )


def test_price_check_can_be_switched_off_by_her() -> None:
    assert decide(listing(price=Decimal(250)), ListingRules(price_must_match=False))[0]
