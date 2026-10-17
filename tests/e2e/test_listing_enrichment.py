"""Listing enrichment, end to end (config A): a photo below the retailer's catalogue standards →
AI-enhanced versions and catalogue copy from her facts → her Guardian approves within her rules or
asks her in the app → the retailer publishes or a person reviews. Scripted model, fake image model:
this tests the flow and every deterministic decision in it, not the model's taste."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from PIL import Image, ImageDraw

from scripts._demo import MAYA, RETAILER, call, reset
from vl_core.a2a.http import make_client
from vl_core.llm import ImageInput, JsonLlm, set_image_gen_override, set_llm_override
from vl_core.settings import Settings

SAID = "40 lavender candles at 220 rupees, 250 grams, scent lavender"

REPLIES: dict[str, dict[str, Any]] = {
    "CatalogueDraft": {
        "sku_name": "lavender candle",
        "category": "candles",
        "unit": "piece",
        "price": "220",
        "qty": "40",
        "claims": [],
        "confidence": 0.9,
    },
    "CraftResult": {
        "is_product": True,
        "grade": "A",
        "finish": "clean",
        "defects": [],
        "batch_consistency": "single_item",
        "visible_claims": [],
        "confidence": 0.9,
    },
    "PhotoReview": {
        "background": "cluttered",
        "other_objects": True,
        "product_size": "good",
        "cut_off": False,
        "lighting": "even",
        "noise": "low",
        "catalogue_ready": False,
        "confidence": 0.8,
    },
    "EditPlan": {
        "product": "a purple lavender candle in a glass jar",
        "keep": ["purple wax", "glass jar", "white label"],
        "edits": ["plain off-white background", "soft daylight from the left", "centred"],
    },
    "Enrichment": {
        "title": "Hand-poured lavender candle, 250 g",
        "description": " ".join(["A calm, hand-poured lavender candle in a glass jar."] * 5),
        "bullets": ["Lavender scent", "250 g glass jar", "Made in small batches"],
        "attributes": [
            {"key": "scent", "value": "lavender", "source": "vendor", "evidence": "lavender"},
            {"key": "size", "value": "250 g", "source": "vendor", "evidence": "250 grams"},
        ],
    },
}


PLAIN_PHOTO = {
    **REPLIES["PhotoReview"],
    "background": "plain",
    "other_objects": False,
    "catalogue_ready": True,
}


class ScriptedModel(JsonLlm):
    """Answers each structured call by its schema, so step order does not matter. A list of
    replies is played in order, one per call (the last one repeats)."""

    model = "scripted"

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = {k: list(v) if isinstance(v, list) else [v] for k, v in replies.items()}

    async def _call(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[ImageInput]
    ) -> str:
        queue = self.replies[json_schema["title"]]
        return json.dumps(queue.pop(0) if len(queue) > 1 else queue[0])


class FakeImageModel:
    model = "fake-image"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def edit(self, prompt: str, photo: ImageInput, aspect_ratio: str = "1:1") -> bytes:
        self.prompts.append(prompt)
        return jpeg(clean=True)


def jpeg(*, clean: bool) -> bytes:
    """A synthetic product photo: a jar on a plain or a busy background."""
    image = Image.new("RGB", (800, 800), (238, 234, 226))
    draw = ImageDraw.Draw(image)
    if not clean:
        for x in range(0, 800, 40):
            draw.line([(x, 0), (800 - x, 800)], fill=(90, 70, 50), width=6)
    draw.rectangle([300, 250, 500, 600], fill=(150, 110, 190), outline=(40, 40, 40), width=4)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


@pytest.fixture
def models() -> Iterator[FakeImageModel]:
    images = FakeImageModel()
    # the photo review: cluttered at first, plain when her chosen version is checked again
    first_then_plain = [REPLIES["PhotoReview"], PLAIN_PHOTO]
    set_llm_override(ScriptedModel({**REPLIES, "PhotoReview": first_then_plain}))
    set_image_gen_override(images)
    yield images
    set_llm_override(None)
    set_image_gen_override(None)


async def draft(s: Settings, photo: bytes, said: str = SAID) -> dict[str, Any]:
    return dict(
        await call(
            s.vendor_svc_url,
            "POST",
            f"/vendors/{MAYA}/listings/draft",
            json={
                "vendor_id": MAYA,
                "language": "en",
                "transcript": said,
                "photo_b64": base64.b64encode(photo).decode(),
                "photo_mime": "image/jpeg",
            },
        )
    )


async def test_ai_photo_goes_to_her_then_to_a_person(
    stack: Settings, models: FakeImageModel
) -> None:
    await reset(stack, "A")
    view = await draft(stack, jpeg(clean=False))
    listing = view["result"]["listing"]

    # The retailer's agents: photo below standard → three enhanced versions, copy from her facts.
    codes = {f["code"] for f in listing["flags"]}
    assert "IMAGE_CLUTTERED_BACKGROUND" in codes
    generated = [c for c in listing["images"] if c["kind"] == "generated"]
    assert len(generated) == 2 and all(c["uri"] and c["preview_b64"] for c in generated)
    enhanced = [c for c in listing["images"] if c["kind"] == "enhanced"]
    assert len(enhanced) == 1 and enhanced[0]["uri"] and enhanced[0]["preview_b64"]  # not AI
    assert all("Keep the product exactly as it is" in p for p in models.prompts)  # code's guard
    assert listing["title"] == "Hand-poured lavender candle, 250 g"
    assert listing["sources"]["price"] == "vendor" and listing["sources"]["size"] == "vendor"

    # Her Guardian may not pick an AI photo for her (default rules): she is asked in the app.
    assert view["approval"] == {
        "auto_approve": False,
        "image": "original",
        "reasons": ["AI_PHOTO_NEEDS_HER_OK"],
    }
    questions = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
    review = next(q for q in questions if q["id"] == f"review-{listing['id']}")
    assert [c["id"] for c in review["request"]["choices"]] == [
        "original",
        "enhanced",
        "img-1",
        "img-2",
        "reject",
    ]

    # She picks photo 2. The retailer's policy sends AI photos to a person before publishing.
    ack = await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/listings/{listing['id']}/confirm",
        json={"image": "img-2"},
    )
    assert ack["ok"] is False
    stored = next(
        x
        for x in await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/listings")
        if x["id"] == listing["id"]
    )
    assert stored["photos"] == [generated[1]["uri"]] and stored["ai_enhanced"] is True
    stored_codes = {f["code"] for f in stored["flags"]}
    assert "AI_PHOTO_NEEDS_REVIEW" in stored_codes
    assert not any(c.startswith("IMAGE_") for c in stored_codes)  # the enhanced photo meets them

    # The category manager approves (Slack in the demo; the queue here) → published.
    queue = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    item = next(q for q in queue if q["ref_id"] == listing["id"])
    await call(stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/queue/{item['id']}/approve")
    published = next(
        x
        for x in await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/listings")
        if x["id"] == listing["id"]
    )
    assert published["status"] == "published"

    # Her side hears it (gateway skill listing.status): her copy is published too.
    mine = next(
        x
        for x in await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/listings")
        if x["id"] == listing["id"]
    )
    assert mine["status"] == "published" and mine["ai_enhanced"] is True

    # The catalogue shows the product with its photos, facts, sources and history.
    cards = await call(
        stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/catalogue?status=published"
    )
    card = next(c for c in cards if c["listing_id"] == listing["id"])
    assert card["ai_enhanced"] and card["thumb_url"].endswith("/images/img-2?size=thumb")
    entry = await call(
        stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/catalogue/{listing['id']}"
    )
    assert [(i["id"], i["kind"], i["chosen"]) for i in entry["images"]] == [
        ("original", "original", False),
        ("enhanced", "enhanced", False),
        ("img-1", "generated", False),
        ("img-2", "generated", True),
    ]
    assert entry["sources"]["size"] == "vendor" and entry["attributes"]["size"] == "250 g"
    actions = [h["action"] for h in entry["history"]]
    assert "listing.built" in actions and "listing.confirmed" in actions
    async with make_client(stack.retailer_svc_url) as client:
        full = await client.get(card["image_url"])
        thumb = await client.get(card["thumb_url"])
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    assert thumb.status_code == 200 and len(thumb.content) < len(full.content)


async def test_her_chosen_photo_is_checked_again(stack: Settings, models: FakeImageModel) -> None:
    """The retailer never assumes a version fixed the photo: the same checker looks again."""
    await reset(stack, "A")
    set_llm_override(ScriptedModel(REPLIES))  # this time the photo still looks cluttered
    view = await draft(stack, jpeg(clean=False))
    listing = view["result"]["listing"]
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/listings/{listing['id']}/confirm",
        json={"image": "enhanced"},
    )
    stored = next(
        x
        for x in await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/listings")
        if x["id"] == listing["id"]
    )
    enhanced_uri = next(c["uri"] for c in stored["images"] if c["kind"] == "enhanced")
    assert stored["photos"] == [enhanced_uri] and stored["ai_enhanced"] is False
    assert "IMAGE_CLUTTERED_BACKGROUND" in {f["code"] for f in stored["flags"]}  # rechecked
    assert stored["status"] == "needs_review"


async def test_her_own_facts_and_a_good_photo_are_approved_for_her(
    stack: Settings, models: FakeImageModel
) -> None:
    await reset(stack, "A")
    models_replies = dict(REPLIES)
    models_replies["PhotoReview"] = {
        **REPLIES["PhotoReview"],
        "background": "plain",
        "other_objects": False,
        "catalogue_ready": True,
    }
    set_llm_override(ScriptedModel(models_replies))
    view = await draft(stack, jpeg(clean=True))
    assert view["approval"]["auto_approve"] is True
    assert models.prompts == []  # nothing to enhance
    listing = view["result"]["listing"]
    mine = next(
        x
        for x in await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/listings")
        if x["id"] == listing["id"]
    )
    assert mine["status"] in ("published", "needs_review")  # sent; the retailer decides
    audit = await call(stack.trust_gateway_url, "GET", "/audit")
    assert any(
        r["action"] == "listing.approved_by_guardian" and r["target"] == listing["id"]
        for r in audit
    )


async def test_a_photo_she_was_not_offered_is_refused(
    stack: Settings, models: FakeImageModel
) -> None:
    await reset(stack, "A")
    view = await draft(stack, jpeg(clean=False))
    listing = view["result"]["listing"]
    bad = await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/listings/{listing['id']}/confirm",
        json={"image": "img-9"},
        expect=422,
    )
    assert bad["detail"]["error"] == "unknown_image"
