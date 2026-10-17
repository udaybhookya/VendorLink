"""Configuration models: everything business-, retailer- or country-specific lives here as data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from vl_core.schemas.common import Fraction, LocalizedText, Money, VLModel

ConfigKind = Literal[
    "business_profiles", "retailer_profiles", "country_packs", "retailer_policies", "workflows"
]


class AttributeSpec(VLModel):
    key: str
    label: LocalizedText
    type: Literal["str", "int", "decimal", "bool", "enum"]
    required: bool = False
    options: list[str] = Field(default_factory=list)


class GradingRules(VLModel):
    scale: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class BusinessProfile(VLModel):
    id: str
    version: int = Field(ge=1)
    name: LocalizedText
    attributes: list[AttributeSpec] = Field(default_factory=list)
    units: list[str]
    grading_rules: GradingRules = Field(default_factory=GradingRules)
    required_docs: list[str] = Field(
        default_factory=list, description="DocSpec roles; the Country Pack names the documents"
    )
    specialists: list[str] = Field(default_factory=list, description="Agent ids to load")


class Category(VLModel):
    id: str
    name: LocalizedText
    units: list[str] = Field(default_factory=list)


class QualityThresholds(VLModel):
    min_grade: str | None = None
    min_shelf_life_days: int | None = Field(default=None, ge=0)
    max_reject_fraction: Fraction | None = None


class ImageLook(VLModel):
    """One styled direction for an AI version of her photo: the backdrop, light, angle and mood.
    The product itself never changes; only how it is staged."""

    id: str
    name: dict[str, str] = Field(description="Shown under the photo, by language code")
    scene: str = Field(description="For the image model: backdrop, surface, light, angle, mood")


class ImageStandards(VLModel):
    """What a catalogue-ready product photo means to this retailer."""

    min_width: int = Field(default=600, ge=1)
    min_height: int = Field(default=600, ge=1)
    min_sharpness: float = Field(
        default=8.0, ge=0, description="Edge strength vs contrast, ~0-100; below = severely blurry"
    )
    min_brightness: float = Field(default=60, ge=0, le=255)
    max_brightness: float = Field(default=235, ge=0, le=255)
    background: str = Field(default="plain, light, uncluttered", description="Style guide words")
    style: str = Field(
        default="product centred, soft daylight, plain light background",
        description="How enhanced photos should look; used in regeneration prompts",
    )
    allow_ai_enhanced: bool = Field(default=True, description="May agents propose AI photos")
    classic_enhancement: bool = Field(
        default=True, description="Also offer her photo cleaned up in code (light, framing)"
    )
    ai_images_need_review: bool = Field(
        default=True, description="A listing using an AI photo goes to a person before publishing"
    )
    candidates: int = Field(default=3, ge=1, le=4, description="Regenerated photos to propose")
    looks: list[ImageLook] = Field(
        default_factory=list,
        description="One look per AI version, in order (repeating if fewer); none = `style` only",
    )
    aspect_ratio: Literal["1:1", "4:5", "3:4", "4:3"] = Field(
        default="1:1", description="Shape of regenerated photos (catalogue tiles)"
    )


class TextStandards(VLModel):
    title_max_chars: int = Field(default=70, ge=10)
    description_min_words: int = Field(default=25, ge=0)
    bullets: int = Field(default=3, ge=0, le=6)


class CatalogueStandards(VLModel):
    """The retailer's bar for a listing: photo and text. Checked by the catalogue specialists."""

    image: ImageStandards = Field(default_factory=ImageStandards)
    text: TextStandards = Field(default_factory=TextStandards)


class TermTemplate(VLModel):
    id: str
    payment_days: int = Field(ge=0)
    description: LocalizedText = Field(default_factory=dict)


class RetailerProfile(VLModel):
    id: str
    version: int = Field(ge=1)
    retailer_type: str
    ordering_mode: Literal["auto_replenish", "manual", "standing_order"]
    categories: list[Category] = Field(default_factory=list)
    quality_thresholds: QualityThresholds = Field(default_factory=QualityThresholds)
    term_templates: list[TermTemplate] = Field(default_factory=list)
    specialists: list[str] = Field(default_factory=list, description="Agent ids to load")
    workflow_set: str = "default"
    claims_requiring_proof: list[str] = Field(
        default_factory=list, description="Product claims flagged for review, e.g. organic"
    )
    catalogue_standards: CatalogueStandards | None = Field(
        default=None, description="Photo and text standards; none means no catalogue checks"
    )


class WorkflowStep(VLModel):
    capability: str = Field(description="Capability or wildcard, e.g. 'grade.*'")
    optional: bool = Field(default=False, description="Skip when no loaded specialist offers it")


class Workflow(VLModel):
    job_type: str
    steps: list[WorkflowStep]


class WorkflowSet(VLModel):
    id: str
    version: int = Field(ge=1)
    workflows: list[Workflow]

    def get(self, job_type: str) -> Workflow | None:
        return next((w for w in self.workflows if w.job_type == job_type), None)


class DocSpec(VLModel):
    """An identity document or food-safety registration, with its number format."""

    id: str
    role: str = Field(description="Generic role a Business Profile requires, e.g. 'identity'")
    name: LocalizedText
    regex: str
    registry: str | None = Field(default=None, description="Mock registry key, e.g. 'fssai'")


class CountryPack(VLModel):
    code: str = Field(pattern=r"^[A-Z]{2}$")
    version: int = Field(ge=1)
    name: LocalizedText
    languages: list[str] = Field(min_length=1)
    default_language: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str
    local_units: list[str] = Field(default_factory=list)
    id_docs: list[DocSpec] = Field(default_factory=list)
    food_safety_regs: list[DocSpec] = Field(default_factory=list)
    consent_wording: LocalizedText = Field(default_factory=dict)
    label_keywords: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Label field → words that introduce it on a label; `halal` → halal marks",
    )


class RetailerPolicy(VLModel):
    id: str
    version: int = Field(ge=1)
    auto_order_max_value: Money = Field(description="In the Country Pack currency")
    auto_accept_counter_max_delta: Fraction = Field(
        description="Max relative change in order value a counter may make and still auto-accept"
    )
    new_vendor_needs_human: bool = True
    listing_publish_min_confidence: Fraction = 0.8


class ConfigRef(VLModel):
    id: str
    version: int | None = Field(default=None, description="None = latest")


class ConfigRefs(VLModel):
    business_profile: ConfigRef
    retailer_profile: ConfigRef
    country_pack: ConfigRef
    retailer_policy: ConfigRef
    workflows: ConfigRef | None = Field(default=None, description="None = profile's set")


class ConfigPreset(VLModel):
    """A named bundle of refs (config A, config B) used for seeding and the demo switch."""

    id: str
    label: str
    refs: ConfigRefs


class ActiveConfig(VLModel):
    retailer_id: str
    refs: ConfigRefs = Field(description="Pinned to concrete versions")
    business_profile: BusinessProfile
    retailer_profile: RetailerProfile
    country_pack: CountryPack
    retailer_policy: RetailerPolicy
    workflows: WorkflowSet
    specialists: list[str]
    config_hash: str
    activated_at: datetime
