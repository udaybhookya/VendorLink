"""image.assess: is the product photo catalogue-ready by the retailer's standards?

Size and brightness are measured in code (`vl_core.imaging`); so is sharpness, as a safety net for
severe blur only (a single sharpness number misjudges real photos: smooth studio backgrounds,
burned-in text). A vision model describes what code cannot measure well (background, clutter,
framing, light, visible blur, noise). Every flag is decided here from the Retailer Profile's
`catalogue_standards`. No standards, no checks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agents.base import Agent, skill
from agents.specialists.catalogue_image_checker.prompts import REVIEW_PHOTO
from vl_core.imaging import PhotoError, PhotoMetrics, measure
from vl_core.llm import ImageInput
from vl_core.schemas import (
    ImageCandidate,
    ImageStandards,
    Listing,
    ListingFlag,
    ListingStepIn,
    ListingStepOut,
)
from vl_core.schemas.common import Fraction


class PhotoReview(BaseModel):
    """What the vision model returns. Internal to this agent: it never crosses a boundary."""

    background: Literal["plain", "busy", "cluttered"]
    other_objects: bool
    product_size: Literal["small", "good"]
    cut_off: bool
    lighting: Literal["even", "harsh", "dim"]
    blur: Literal["none", "slight", "strong"] = "none"
    noise: Literal["low", "medium", "high"]
    catalogue_ready: bool
    confidence: Fraction


def measured_flags(m: PhotoMetrics, std: ImageStandards) -> list[ListingFlag]:
    flags = []
    if m.width < std.min_width or m.height < std.min_height:
        flags.append(
            ListingFlag(
                code="IMAGE_LOW_RESOLUTION",
                detail=f"{m.width}×{m.height} < {std.min_width}×{std.min_height}",
            )
        )
    if m.sharpness < std.min_sharpness:
        detail = f"sharpness {m.sharpness} < {std.min_sharpness}"
        flags.append(ListingFlag(code="IMAGE_BLURRY", detail=detail))
    if m.brightness < std.min_brightness:
        flags.append(ListingFlag(code="IMAGE_TOO_DARK", detail=f"brightness {m.brightness}"))
    elif m.brightness > std.max_brightness:
        flags.append(ListingFlag(code="IMAGE_TOO_BRIGHT", detail=f"brightness {m.brightness}"))
    return flags


def described_flags(r: PhotoReview) -> list[ListingFlag]:
    flags = []
    if r.background != "plain" or r.other_objects:
        flags.append(ListingFlag(code="IMAGE_CLUTTERED_BACKGROUND", detail=r.background))
    if r.product_size == "small":
        flags.append(ListingFlag(code="IMAGE_PRODUCT_TOO_SMALL"))
    if r.cut_off:
        flags.append(ListingFlag(code="IMAGE_PRODUCT_CUT_OFF"))
    if r.lighting != "even":
        flags.append(ListingFlag(code="IMAGE_POOR_LIGHTING", detail=r.lighting))
    if r.blur == "strong":
        flags.append(ListingFlag(code="IMAGE_BLURRY", detail="visibly out of focus"))
    if r.noise == "high":
        flags.append(ListingFlag(code="IMAGE_NOISY"))
    if not r.catalogue_ready and not flags:
        flags.append(ListingFlag(code="IMAGE_NOT_CATALOGUE_READY"))
    return flags


class CatalogueImageChecker(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"

    @skill("image.assess")
    async def check(self, inp: ListingStepIn) -> ListingStepOut:
        standards = inp.retailer_profile.catalogue_standards
        if inp.listing is None or standards is None or not inp.photo_uri:
            return ListingStepOut(listing=inp.listing)  # nothing to check against, or no photo
        std = standards.image
        data = await self.ctx.blob().get(inp.photo_uri)
        try:
            metrics = measure(data)
        except PhotoError as exc:
            flag = ListingFlag(code="IMAGE_UNREADABLE", detail=str(exc)[:200])
            listing = inp.listing.model_copy(update={"flags": [*inp.listing.flags, flag]})
            return self._out(listing, [flag])
        flags = measured_flags(metrics, std)
        prompt = REVIEW_PHOTO.substitute(background=std.background, style=std.style)
        result = await self.ctx.llm(self.options.llm_tier).generate_json(
            prompt, PhotoReview, [ImageInput(data)]
        )
        if result.value is None:
            flags.append(ListingFlag(code="IMAGE_NEEDS_REVIEW", detail=result.error or ""))
        else:
            flags += described_flags(result.value)
        listing = inp.listing
        seen = {f.code for f in listing.flags}
        flags = [f for f in flags if f.code not in seen]
        flags = list({f.code: f for f in reversed(flags)}.values())[::-1]  # measured wins
        original = ImageCandidate(id="original", kind="original", uri=inp.photo_uri)
        updated = listing.model_copy(
            update={
                "flags": [*listing.flags, *flags],
                "images": listing.images or [original],
                "attributes": {
                    **listing.attributes,
                    "photo_size": f"{metrics.width}x{metrics.height}",
                    "photo_sharpness": metrics.sharpness,
                },
            }
        )
        self.ctx.log().info(
            "photo checked",
            size=f"{metrics.width}x{metrics.height}",
            sharpness=metrics.sharpness,
            brightness=metrics.brightness,
            flags=",".join(f.code for f in flags) or None,
        )
        return self._out(updated, flags)

    @staticmethod
    def _out(listing: Listing, flags: list[ListingFlag]) -> ListingStepOut:
        return ListingStepOut(listing=listing, flags=flags, needs_review=bool(flags))
