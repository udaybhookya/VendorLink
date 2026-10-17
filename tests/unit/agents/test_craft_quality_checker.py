"""Craft Quality Checker: the model only describes the photo; every flag is decided from config."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agents.registry import AgentRegistry
from tests.conftest import CONFIG_DIR
from vl_core.config import ConfigLoader, resolve_active_config
from vl_core.llm import FakeLlm, set_llm_override
from vl_core.schemas import ActiveConfig, Listing, ListingFlag, ListingStepIn, ListingStepOut
from vl_core.settings import Settings
from vl_core.store.blob import LocalBlobStore

CARD = "craft-quality-checker"
GOOD = {
    "is_product": True,
    "grade": "A",
    "finish": "clean",
    "defects": [],
    "batch_consistency": "consistent",
    "visible_claims": [],
    "confidence": 0.9,
}


@pytest.fixture
def active() -> ActiveConfig:
    loader = ConfigLoader(CONFIG_DIR)
    return resolve_active_config("r", loader.load_preset("A").refs, loader)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("VL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VL_BLOB", "local")
    return Settings()


@pytest.fixture(autouse=True)
def no_model() -> Iterator[None]:
    yield
    set_llm_override(None)


async def check(
    settings: Settings,
    active: ActiveConfig,
    seen: dict[str, Any] | None,
    *,
    transcript: str = "",
    photo: bool = True,
    flags: tuple[str, ...] = (),
) -> ListingStepOut:
    set_llm_override(FakeLlm([json.dumps({**GOOD, **seen})] if seen is not None else []))
    uri = None
    if photo:
        uri = await LocalBlobStore(settings.vl_data_dir / "blobs").put(
            "photos/candle.jpg", b"jpeg", "image/jpeg"
        )
    listing = Listing(
        id="l",
        vendor_id="vendor:maya",
        sku_name="lavender candle",
        category="candles",
        unit="piece",
        price=Decimal(220),
        currency="INR",
        qty=Decimal(40),
        confidence=0.95,
        flags=[ListingFlag(code=f) for f in flags],
    )
    step = ListingStepIn(
        vendor_id="vendor:maya",
        language="en",
        photo_uri=uri,
        transcript=transcript,
        listing=listing,
        business_profile=active.business_profile,
        retailer_profile=active.retailer_profile,
        country_pack=active.country_pack,
    )
    registry = AgentRegistry.load(settings, host="retailer-svc")
    return ListingStepOut.model_validate(await registry.invoke(CARD, "grade.craft", step))


def codes(out: ListingStepOut) -> set[str]:
    return {f.code for f in out.flags}


async def test_clean_candle_passes_with_its_details(
    settings: Settings, active: ActiveConfig
) -> None:
    out = await check(settings, active, {})
    assert codes(out) == set() and not out.needs_review
    assert out.listing is not None and out.listing.grade == "A"
    assert out.listing.attributes["finish"] == "clean"
    assert out.listing.confidence == 0.9  # the lower of the draft's and the model's


async def test_below_min_grade_and_blocking_defects(
    settings: Settings, active: ActiveConfig
) -> None:
    out = await check(settings, active, {"grade": "C", "defects": ["cracks", "frosting", "cracks"]})
    # frosting is cosmetic (not in blocking_defects); cracks block, once
    assert codes(out) == {"BELOW_MIN_GRADE", "DEFECT_CRACKS"} and out.needs_review


async def test_inconsistent_batch_is_flagged(settings: Settings, active: ActiveConfig) -> None:
    out = await check(settings, active, {"batch_consistency": "inconsistent"})
    assert codes(out) == {"BATCH_INCONSISTENT"}


async def test_claims_from_words_and_label_need_proof(
    settings: Settings, active: ActiveConfig
) -> None:
    out = await check(
        settings,
        active,
        {"visible_claims": ["100% Soy Wax"]},
        transcript="handmade lavender candles",
    )
    assert codes(out) == {"UNSUPPORTED_CLAIM_SOY_WAX", "UNSUPPORTED_CLAIM_HANDMADE"}


async def test_flags_already_raised_upstream_are_not_repeated(
    settings: Settings, active: ActiveConfig
) -> None:
    out = await check(
        settings,
        active,
        {},
        transcript="handmade candles",
        flags=("UNSUPPORTED_CLAIM_HANDMADE",),
    )
    assert codes(out) == set()
    assert out.listing is not None
    assert [f.code for f in out.listing.flags] == ["UNSUPPORTED_CLAIM_HANDMADE"]


async def test_not_a_product_photo(settings: Settings, active: ActiveConfig) -> None:
    out = await check(settings, active, {"is_product": False, "grade": "C", "confidence": 0})
    assert {"NOT_A_PRODUCT_PHOTO", "BELOW_MIN_GRADE"} <= codes(out)


async def test_no_photo_still_checks_spoken_claims(
    settings: Settings, active: ActiveConfig
) -> None:
    out = await check(settings, active, None, photo=False, transcript="natural soy wax")
    assert codes(out) == {"NO_PHOTO", "UNSUPPORTED_CLAIM_SOY_WAX", "UNSUPPORTED_CLAIM_NATURAL"}


async def test_model_failure_needs_review_never_a_default(
    settings: Settings, active: ActiveConfig
) -> None:
    out = await check(settings, active, None)  # the fake model has nothing to say
    assert codes(out) == {"CRAFT_NEEDS_REVIEW"} and out.needs_review
    assert out.listing is not None and out.listing.grade is None
