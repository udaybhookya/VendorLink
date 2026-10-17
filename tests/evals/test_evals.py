"""Specialist eval sets. Each must score ≥ 8/10 (CLAUDE.md). Cases live next to this file."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from agents.registry import AgentRegistry
from scripts._stack import build_stack, teardown_stack
from tests.conftest import CONFIG_DIR, ROOT
from vl_core.config import ConfigLoader
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
                "order.replenish",
                OrderPlanIn.model_validate(
                    {
                        "retailer_id": "retailer:freshcart",
                        "vendor_id": "vendor:maya",
                        "store_id": "store:hub-4",
                        "for_date": date(2026, 10, 15),
                        "listings": listings,
                        "country_pack": cp("IN"),
                        "terms_version": "qc-standard-v1",
                        "payment_days": 7,
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
