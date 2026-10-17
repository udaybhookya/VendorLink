"""image.enhance: a photo below the catalogue standards → better versions of her own photo.

Two kinds: one auto-enhanced in code (exposure, contrast, framing; not AI, always offered when the
retailer allows it) and AI-regenerated versions (as many as the retailer's `candidates`).

Each AI version is staged in a different look from the retailer's profile (`looks`: backdrop,
light, angle, mood), so she chooses between real alternatives, not near-copies. A vision model
looks at her photo and plans (what the product is, what must stay the same, what to fix, and each
look adapted to this product). Code wraps every version in a fixed faithfulness guard, then the
image model renders it from her photo (never from scratch). Without an image model the plans stay
as prompts, and the app shows her reshoot tips instead. She always sees her original next to the
versions.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Literal

from pydantic import BaseModel, Field

from agents.base import Agent, skill
from agents.specialists.image_enhancer.prompts import FAITHFUL_EDIT, PLAN_EDITS
from vl_core.imaging import PhotoError, as_jpeg, auto_enhance, preview
from vl_core.llm import ImageInput
from vl_core.schemas import (
    ImageCandidate,
    ImageLook,
    ImageStandards,
    ListingFlag,
    ListingStepIn,
    ListingStepOut,
)

PHOTO_FLAG = "IMAGE_"


class EditPlan(BaseModel):
    """What the vision model returns. Internal to this agent: it never crosses a boundary."""

    product: str
    keep: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list, description="One per look, in order")


class Version(BaseModel):
    """One AI version to render: its look and its guarded prompt."""

    look: ImageLook
    prompt: str


def looks_for(std: ImageStandards) -> list[ImageLook]:
    """The look of each version: the retailer's looks in order (repeating if it wants more versions
    than it has looks); without looks, every version in the retailer's one `style`."""
    looks = std.looks or [ImageLook(id="style", name={}, scene=std.style)]
    return [looks[n % len(looks)] for n in range(std.candidates)]


def rule_edits(problems: list[str]) -> list[str]:
    """Edits from the flags alone, used when the planning model fails (flagged, never silent)."""
    edits = []
    if any(p in problems for p in ("IMAGE_CLUTTERED_BACKGROUND", "IMAGE_NOT_CATALOGUE_READY")):
        edits.append("replace the background with a plain, light, even surface")
    if any(p in problems for p in ("IMAGE_TOO_DARK", "IMAGE_POOR_LIGHTING", "IMAGE_TOO_BRIGHT")):
        edits.append("relight with soft, even daylight from the front")
    if any(p in problems for p in ("IMAGE_BLURRY", "IMAGE_NOISY", "IMAGE_LOW_RESOLUTION")):
        edits.append("make the product sharp and clean, without grain")
    if "IMAGE_PRODUCT_TOO_SMALL" in problems:
        edits.append("crop so the product fills most of the frame, centred")
    if "IMAGE_PRODUCT_CUT_OFF" in problems:
        edits.append("zoom out so the whole product is in the frame, on a plain background")
    return edits or ["keep the product sharp, centred and fully in frame"]


class ImageEnhancer(Agent):
    class Options(BaseModel):
        llm_tier: Literal["flash", "pro"] = "flash"

    @skill("image.enhance")
    async def enhance(self, inp: ListingStepIn) -> ListingStepOut:
        listing = inp.listing
        standards = inp.retailer_profile.catalogue_standards
        if listing is None or standards is None or not inp.photo_uri:
            return ListingStepOut(listing=listing)
        std = standards.image
        problems = [f.code for f in listing.flags if f.code.startswith(PHOTO_FLAG)]
        if not problems or "IMAGE_UNREADABLE" in problems:
            return ListingStepOut(listing=listing)  # the photo is fine, or nothing to work from
        if not (std.allow_ai_enhanced or std.classic_enhancement):
            return ListingStepOut(listing=listing)
        data = await self.ctx.blob().get(inp.photo_uri)
        flags: list[ListingFlag] = []
        candidates: list[ImageCandidate] = []
        if std.classic_enhancement:
            candidates.append(await self._classic(data, inp.vendor_id, listing.id))
        versions: list[Version] = []
        if std.allow_ai_enhanced:
            versions = await self._plan(data, problems, std, listing.sku_name, flags)
            photo = ImageInput(data)
            candidates += await self._render(
                versions, photo, std.aspect_ratio, inp.vendor_id, listing.id, flags
            )
        original = ImageCandidate(
            id="original", kind="original", uri=inp.photo_uri, preview_b64=_preview_b64(data)
        )
        updated = listing.model_copy(
            update={"images": [original, *candidates], "flags": [*listing.flags, *flags]}
        )
        self.ctx.log().info(
            "photo versions prepared",
            enhanced=sum(1 for c in candidates if c.kind == "enhanced"),
            prompts=len(versions),
            looks=",".join(v.look.id for v in versions) or None,
            rendered=sum(1 for c in candidates if c.kind == "generated" and c.uri),
            model=getattr(self.ctx.images(), "model", None) or "none (reshoot tips)",
        )
        return ListingStepOut(listing=updated, flags=flags, needs_review=bool(flags))

    async def _classic(self, data: bytes, vendor_id: str, listing_id: str) -> ImageCandidate:
        """Her photo cleaned up in code (exposure, contrast, framing). Not AI; always offered."""
        image = await asyncio.to_thread(auto_enhance, data)
        uri = await self.ctx.blob().put(
            f"listings/{vendor_id}/{listing_id}-enhanced.jpg", image, "image/jpeg"
        )
        return ImageCandidate(
            id="enhanced", kind="enhanced", uri=uri, preview_b64=_preview_b64(image)
        )

    async def _plan(
        self,
        data: bytes,
        problems: list[str],
        std: ImageStandards,
        product: str,
        flags: list[ListingFlag],
    ) -> list[Version]:
        """One version per look. The model says what to keep and fix and adapts each look to this
        product; code wraps each in the faithfulness guard. The looks hold even if it fails."""
        looks = looks_for(std)
        result = await self.ctx.llm(self.options.llm_tier).generate_json(
            PLAN_EDITS.substitute(
                problems=", ".join(problems),
                style=std.style,
                background=std.background,
                count=len(looks),
                looks="\n".join(f"{n}. {lk.scene}" for n, lk in enumerate(looks, start=1)),
            ),
            EditPlan,
            [ImageInput(data)],
        )
        plan = result.value
        if plan is None:
            flags.append(ListingFlag(code="IMAGE_EDITS_FROM_RULES", detail=result.error or ""))
            plan = EditPlan(product=product, fixes=rule_edits(problems))
        fixes = plan.fixes[:3] or rule_edits(problems)
        return [
            Version(
                look=look,
                prompt=FAITHFUL_EDIT.substitute(
                    product=(plan.product or product).rstrip(". "),
                    keep=", ".join(plan.keep) or "everything about the product",
                    fixes="; ".join(fixes),
                    look=look.name.get("en", look.id),
                    scene=look.scene.rstrip(".") + ".",
                    detail=plan.scenes[n] if n < len(plan.scenes) else "",
                ).strip(),
            )
            for n, look in enumerate(looks)
        ]

    async def _render(
        self,
        versions: list[Version],
        photo: ImageInput,
        aspect_ratio: str,
        vendor_id: str,
        listing_id: str,
        flags: list[ListingFlag],
    ) -> list[ImageCandidate]:
        """Render every version from her photo, in parallel, in the retailer's catalogue shape.
        Without an image model the prompts stay prompts (the app shows retake tips)."""
        gen = self.ctx.images()

        async def one(n: int, version: Version) -> ImageCandidate:
            uri = shown = None
            if gen is not None:
                try:
                    image = as_jpeg(await gen.edit(version.prompt, photo, aspect_ratio))
                    uri = await self.ctx.blob().put(
                        f"listings/{vendor_id}/{listing_id}-img-{n}.jpg", image, "image/jpeg"
                    )
                    shown = _preview_b64(image)
                except Exception as exc:  # one failed render must not lose the others
                    self.ctx.log().warning("photo version failed", n=n, error=repr(exc)[:300])
            return ImageCandidate(
                id=f"img-{n}",
                kind="generated",
                uri=uri,
                prompt=version.prompt,
                preview_b64=shown,
                look=version.look.id if version.look.name else "",
                look_name=version.look.name,
            )

        candidates = list(
            await asyncio.gather(*(one(n, v) for n, v in enumerate(versions, start=1)))
        )
        if gen is not None and any(c.uri is None for c in candidates):
            flags.append(ListingFlag(code="IMAGE_GENERATION_FAILED"))
        return candidates


def _preview_b64(data: bytes) -> str | None:
    try:
        return base64.b64encode(preview(data)).decode()
    except PhotoError:
        return None
