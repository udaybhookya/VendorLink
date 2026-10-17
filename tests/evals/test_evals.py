"""Specialist eval sets. Each must score ≥ 8/10 (CLAUDE.md). Cases live next to this file."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from agents.registry import AgentRegistry
from scripts._stack import build_stack, teardown_stack
from tests.conftest import CONFIG_DIR, ROOT
from vl_core.config import ConfigLoader
from vl_core.llm import ImageInput, JsonLlm, set_image_gen_override, set_llm_override
from vl_core.schemas import (
    BusinessProfile,
    CountryPack,
    Listing,
    ListingFlag,
    ListingStepIn,
    ListingStepOut,
    OrderPlanIn,
    OrderPlanOut,
    RetailerProfile,
    Vendor,
    VendorCheckIn,
    VendorCheckOut,
)
from vl_core.schemas.common import _decimal_str
from vl_core.settings import get_settings
from vl_core.store import get_blob_store
from vl_core.store.blob import LocalBlobStore

HERE = Path(__file__).parent
PASS_MARK = 0.8
loader = ConfigLoader(CONFIG_DIR)
registry = AgentRegistry.load()


def cases(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((HERE / name / "cases.json").read_text()))


def bp(i: str) -> BusinessProfile:
    return cast(BusinessProfile, loader.load("business_profiles", i))


def cp(i: str) -> CountryPack:
    return cast(CountryPack, loader.load("country_packs", i))


def rp(i: str) -> RetailerProfile:
    return cast(RetailerProfile, loader.load("retailer_profiles", i))


async def score(
    name: str, items: list[dict[str, Any]], check: Callable[[dict[str, Any]], Any]
) -> None:
    failures = []
    for case in items:
        try:
            ok = await check(case)
        except Exception as exc:  # a crash is a failed case, not an aborted eval
            ok, case = False, {**case, "error": repr(exc)}
        if not ok:
            failures.append(case.get("name"))
    result = 1 - len(failures) / len(items)
    print(f"{name}: {len(items) - len(failures)}/{len(items)} failures={failures}")
    assert result >= PASS_MARK, f"{name} scored {result:.0%}; failed: {failures}"


@pytest.fixture
def mocks_online(tmp_path: Path) -> Iterator[None]:
    build_stack(tmp_path)
    yield
    teardown_stack()


async def test_label_checker() -> None:
    data = cases("label_checker")
    card = "label-checker"

    async def check(case: dict[str, Any]) -> bool:
        listing = Listing(
            id="l",
            vendor_id="v",
            sku_name="x",
            category="snacks",
            unit="pack",
            price=Decimal(1),
            currency="IDR",
            qty=Decimal(1),
            confidence=0.9,
        )
        out = ListingStepOut.model_validate(
            await registry.invoke(
                card,
                "grade.label",
                ListingStepIn(
                    vendor_id="v",
                    language="id",
                    label_text=case["label"],
                    listing=listing,
                    business_profile=bp(data["business_profile"]),
                    retailer_profile=rp("grocery_chain"),
                    country_pack=cp(data["country"]),
                ),
            )
        )
        return {f.code for f in out.flags} == set(case["expect"])

    await score("label_checker", data["cases"], check)


async def test_catalogue_builder() -> None:
    data = cases("catalogue_builder")
    card = "catalogue-builder"
    without_model: list[str] = []  # the model failed (quota, outage) and the parser answered

    async def check(case: dict[str, Any]) -> bool:
        out = ListingStepOut.model_validate(
            await registry.invoke(
                card,
                "catalogue.build",
                ListingStepIn(
                    vendor_id="v",
                    language="en",
                    transcript=case["transcript"],
                    business_profile=bp(data["business_profile"]),
                    retailer_profile=rp(data["retailer_profile"]),
                    country_pack=cp(data["country"]),
                ),
            )
        )
        expect = case["expect"]
        if "listing" in expect and expect["listing"] is None:
            return out.listing is None and out.needs_review
        assert out.listing is not None
        got = out.listing.model_dump(mode="json")
        codes = {f.code for f in out.flags}
        if "PARSED_WITHOUT_MODEL" in codes:
            without_model.append(case["name"])
        return all(set(v) <= codes if k == "flags" else got.get(k) == v for k, v in expect.items())

    await score("catalogue_builder", data["cases"], check)
    if without_model:
        print(f"  catalogue_builder: answered without the model={without_model}")


async def test_onboarding_verifier(mocks_online: None) -> None:
    card = "onboarding-verifier"

    async def check(case: dict[str, Any]) -> bool:
        vendor = Vendor(
            id="vendor:x",
            display_name="x",
            business_profile=case["bp"],
            country=case["country"],
            language="en",
        )
        out = VendorCheckOut.model_validate(
            await registry.invoke(
                card,
                "vendor.verify",
                VendorCheckIn(
                    vendor=vendor,
                    legal_name=case["name_given"],
                    doc_numbers=case["docs"],
                    business_profile=bp(case["bp"]),
                    country_pack=cp(case["country"]),
                ),
            )
        )
        codes = {c.doc_type: c.code for c in out.checks}
        return out.passed == case["passed"] and all(
            codes.get(k) == v for k, v in case["codes"].items()
        )

    await score("onboarding_verifier", cases("onboarding_verifier")["cases"], check)


async def test_compliance_auditor() -> None:
    card = "compliance-auditor"

    async def check(case: dict[str, Any]) -> bool:
        listing = Listing(
            id="l1",
            vendor_id="vendor:x",
            sku_name="x",
            category="c",
            unit="pack",
            price=Decimal(1),
            currency="X",
            qty=Decimal(1),
            flags=[ListingFlag(code=f) for f in case["listing_flags"]],
        )
        vendor = Vendor(
            id="vendor:x",
            display_name="x",
            business_profile=case["bp"],
            country=case["country"],
            language="en",
        )
        out = VendorCheckOut.model_validate(
            await registry.invoke(
                card,
                "compliance.check",
                VendorCheckIn(
                    vendor=vendor,
                    legal_name="x",
                    doc_numbers=case["docs"],
                    business_profile=bp(case["bp"]),
                    country_pack=cp(case["country"]),
                    listings=[listing],
                ),
            )
        )
        return out.passed == case["passed"]

    await score("compliance_auditor", cases("compliance_auditor")["cases"], check)


async def test_replenishment_planner() -> None:
    card = "replenishment-planner"
    seed = json.loads((ROOT / "seed" / "retailer.json").read_text())
    listings = [Listing.model_validate(item) for item in seed["listings"]]

    async def check(case: dict[str, Any]) -> bool:
        out = OrderPlanOut.model_validate(
            await registry.invoke(
                card,
                "order.create",
                OrderPlanIn.model_validate(
                    {
                        "retailer_id": "retailer:freshcart",
                        "vendor_id": "vendor:maya",
                        "store_id": "store:hub-4",
                        "for_date": date(2026, 10, 15),
                        "listings": listings,
                        "country_pack": cp("IN"),
                        "terms_version": "gift-corporate-v1",
                        "payment_days": 45,
                        "demand_override": case["demand"],
                    }
                ),
            )
        )
        if not case["lines"]:
            return out.proposals == []
        p = out.proposals[0]
        lines = [[line.sku, _decimal_str(line.qty), _decimal_str(line.price)] for line in p.lines]
        pickup = p.pickup_at.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M")
        return lines == case["lines"] and pickup == case["pickup_local"]

    await score("replenishment_planner", cases("replenishment_planner")["cases"], check)


async def test_produce_grader_needs_photos_and_model() -> None:
    data = cases("produce_grader")
    missing = [c["photo"] for c in data["cases"] if not (ROOT / c["photo"]).exists()]
    if missing or not get_settings().flash_model:
        pytest.skip(f"needs FLASH_MODEL and {len(missing)} photos in seed/photos/ (see cases.json)")


async def test_craft_quality_checker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = cases("craft_quality_checker")
    missing = [c["photo"] for c in data["cases"] if not (ROOT / c["photo"]).exists()]
    if missing or not get_settings().flash_model:
        pytest.skip(f"needs FLASH_MODEL and {len(missing)} photos in seed/photos/ (see cases.json)")
    monkeypatch.setenv("VL_BLOB", "local")
    monkeypatch.setenv("VL_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    settings = get_settings()
    local = AgentRegistry.load(settings, host="retailer-svc")
    blobs = LocalBlobStore(settings.vl_data_dir / "blobs")
    business, retailer = bp("handmade_gifts"), rp("gifting")

    def matches(code: str, pattern: str) -> bool:
        return code.startswith(pattern[:-1]) if pattern.endswith("*") else code == pattern

    async def check(case: dict[str, Any]) -> bool:
        photo = ROOT / case["photo"]
        uri = await blobs.put(f"evals/{photo.name}", photo.read_bytes(), "image/jpeg")
        listing = Listing(
            id="l",
            vendor_id="v",
            sku_name="candle",
            category="candles",
            unit="piece",
            price=Decimal(220),
            currency="INR",
            qty=Decimal(1),
            confidence=1.0,
        )
        out = ListingStepOut.model_validate(
            await local.invoke(
                "craft-quality-checker",
                "grade.craft",
                ListingStepIn(
                    vendor_id="v",
                    language="en",
                    photo_uri=uri,
                    transcript=case.get("transcript", ""),
                    listing=listing,
                    business_profile=business,
                    retailer_profile=retailer,
                    country_pack=cp("IN"),
                ),
            )
        )
        codes = {f.code for f in out.flags}
        grade = out.listing.grade if out.listing else None
        ok = (
            (not case["grades"] or grade in case["grades"])
            and all(code in codes for code in case["require"])
            and not any(matches(c, p) for c in codes for p in case["forbid"])
        )
        if not ok:
            print(f"  {case['name']}: grade={grade} flags={sorted(codes)}")
        return ok

    await score("craft_quality_checker", data["cases"], check)


# ---- catalogue enrichment: offline evals of every deterministic verdict ----------------------


class ScriptedModel(JsonLlm):
    """Answers each structured call by its schema; `None` plays a model that fails."""

    model = "scripted"

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies

    async def _call(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[ImageInput]
    ) -> str:
        reply = self.replies.get(json_schema["title"])
        return "not json" if reply is None else json.dumps(reply)


class FakeImageModel:
    model = "fake-image"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def edit(self, prompt: str, photo: ImageInput, aspect_ratio: str = "1:1") -> bytes:
        if self.fail:
            raise RuntimeError("image model unavailable")
        return synthetic_photo({})


def synthetic_photo(spec: dict[str, Any]) -> bytes:
    """A jar on a plain background; `size`, `blur` and `light` change what is measured."""
    if "bytes" in spec:
        return str(spec["bytes"]).encode()
    size = int(spec.get("size", 800))
    image = Image.new("RGB", (size, size), (225, 220, 210))
    draw = ImageDraw.Draw(image)
    s = size / 800
    draw.rectangle([300 * s, 250 * s, 500 * s, 600 * s], fill=(150, 110, 190), outline=(30, 30, 30))
    for y in range(int(300 * s), int(560 * s), max(1, int(24 * s))):
        draw.line([(320 * s, y), (480 * s, y)], fill=(250, 250, 250), width=max(1, int(3 * s)))
    if spec.get("blur"):
        image = image.filter(ImageFilter.GaussianBlur(spec["blur"]))
    if spec.get("light"):
        image = ImageEnhance.Brightness(image).enhance(spec["light"])
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


@pytest.fixture
def retailer_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AgentRegistry]:
    monkeypatch.setenv("VL_BLOB", "local")
    monkeypatch.setenv("VL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IMAGE_MODEL", "")  # offline evals: images come from FakeImageModel only
    get_settings.cache_clear()
    yield AgentRegistry.load(get_settings(), host="retailer-svc")
    set_llm_override(None)
    set_image_gen_override(None)
    get_settings.cache_clear()


def candle(**changes: Any) -> Listing:
    return Listing(
        id="l",
        vendor_id="v",
        sku_name="lavender candle",
        category="candles",
        unit="piece",
        price=Decimal(220),
        currency="INR",
        qty=Decimal(40),
        confidence=0.9,
    ).model_copy(update=changes)


def step(listing: Listing, uri: str | None, said: str = "", **changes: Any) -> ListingStepIn:
    return ListingStepIn(
        vendor_id="v",
        language="en",
        photo_uri=uri,
        transcript=said,
        listing=listing,
        business_profile=bp("handmade_gifts"),
        retailer_profile=changes.pop("retailer", rp("gifting")),
        country_pack=cp("IN"),
        **changes,
    )


async def test_catalogue_image_checker(retailer_host: AgentRegistry) -> None:
    data = cases("catalogue_image_checker")
    blobs = get_blob_store(get_settings())

    async def check(case: dict[str, Any]) -> bool:
        review = case["review"]
        set_llm_override(
            ScriptedModel(
                {"PhotoReview": None if review is None else {**data["default_review"], **review}}
            )
        )
        photo = synthetic_photo(case["photo"])
        uri = await blobs.put(f"evals/{case['name']}.jpg", photo, "image/jpeg")
        out = ListingStepOut.model_validate(
            await retailer_host.invoke(
                "catalogue-image-checker", "image.assess", step(candle(), uri)
            )
        )
        codes = {f.code for f in out.flags}
        if codes != set(case["expect"]):
            print(f"  {case['name']}: {sorted(codes)}")
        return codes == set(case["expect"])

    await score("catalogue_image_checker", data["cases"], check)


async def test_image_enhancer(retailer_host: AgentRegistry) -> None:
    data = cases("image_enhancer")
    blobs = get_blob_store(get_settings())
    uri = await blobs.put("evals/raw.jpg", synthetic_photo({}), "image/jpeg")
    gifting = rp("gifting")
    assert gifting.catalogue_standards is not None

    async def check(case: dict[str, Any]) -> bool:
        plan = case.get("plan", data["default_plan"])
        set_llm_override(ScriptedModel({"EditPlan": plan}))
        gen = case.get("gen", True)
        set_image_gen_override(FakeImageModel(fail=gen == "fail") if gen else None)
        image = gifting.catalogue_standards.image.model_copy(update=case.get("standards", {}))
        retailer = gifting.model_copy(
            update={
                "catalogue_standards": gifting.catalogue_standards.model_copy(
                    update={"image": image}
                )
            }
        )
        listing = candle(flags=[ListingFlag(code=c) for c in case["flags"]])
        out = ListingStepOut.model_validate(
            await retailer_host.invoke(
                "image-enhancer", "image.enhance", step(listing, uri, retailer=retailer)
            )
        )
        made = [c for c in (out.listing.images if out.listing else []) if c.kind == "generated"]
        guarded = all("Keep the product exactly as it is" in c.prompt for c in made)
        # each version is staged in its own look, so she chooses between real alternatives
        if len(image.looks) >= len(made):
            guarded = guarded and len({c.look for c in made}) == len(made)
            guarded = guarded and all(c.look_name.get("en", "") in c.prompt for c in made)
        images = out.listing.images if out.listing else []
        got = {
            "rendered": sum(1 for c in made if c.uri and c.preview_b64),
            "prompts": len(made),
            "enhanced": sum(1 for c in images if c.kind == "enhanced" and c.uri and c.preview_b64),
            "flags": sorted(f.code for f in out.flags),
        }
        ok = guarded and got == {**case["expect"], "flags": sorted(case["expect"]["flags"])}
        if not ok:
            print(f"  {case['name']}: {got} guarded={guarded}")
        return ok

    await score("image_enhancer", data["cases"], check)


async def test_listing_enricher(retailer_host: AgentRegistry) -> None:
    data = cases("listing_enricher")

    async def check(case: dict[str, Any]) -> bool:
        reply = case.get("reply", {})
        set_llm_override(
            ScriptedModel({"Enrichment": None if reply is None else {**data["default"], **reply}})
        )
        out = ListingStepOut.model_validate(
            await retailer_host.invoke(
                "listing-enricher",
                "listing.enrich",
                step(candle(), None, case["said"], answers=case.get("answers", {})),
            )
        )
        listing = out.listing
        assert listing is not None
        expect = case["expect"]
        problems = []
        if sorted(f.code for f in out.flags) != sorted(expect["flags"]):
            problems.append(f"flags={sorted(f.code for f in out.flags)}")
        for key, source in expect.get("sources", {}).items():
            if listing.sources.get(key) != source:
                problems.append(f"{key} source={listing.sources.get(key)}")
        for key, value in expect.get("attributes", {}).items():
            if listing.attributes.get(key) != value:
                problems.append(f"{key}={listing.attributes.get(key)}")
        problems += [f"{k} present" for k in expect.get("absent", []) if k in listing.attributes]
        if "title_max" in expect and len(listing.title) > expect["title_max"]:
            problems.append(f"title {len(listing.title)} chars")
        if problems:
            print(f"  {case['name']}: {', '.join(problems)}")
        return not problems

    await score("listing_enricher", data["cases"], check)
