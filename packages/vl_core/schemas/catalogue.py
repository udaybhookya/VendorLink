"""The retailer's catalogue: published (and in-review) listings with their photos, copy, where
each fact came from, what happened to them, and the orders placed for them. Also the status the
retailer sends back to the vendor's side (gateway skill `listing.status`)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import Money, Qty, VLModel
from vl_core.schemas.vendor import FactSource, ListingFlag

ListingStatus = Literal["draft", "needs_review", "published", "rejected"]


class ListingStatusUpdate(VLModel):
    """Retailer → vendor (gateway skill listing.status): what became of her listing, so her copy
    stays true when a person decides later (review queue)."""

    listing_id: str
    vendor_id: str
    retailer_id: str
    status: ListingStatus
    reasons: list[str] = Field(default_factory=list, description="Machine codes, if not published")
    ai_enhanced: bool = False


class CatalogueImage(VLModel):
    id: str = Field(description="original, enhanced, img-1 …")
    kind: Literal["original", "enhanced", "generated"]
    url: str | None = Field(default=None, description="Full image, served by the retailer API")
    thumb_url: str | None = None
    prompt: str = Field(default="", description="The regeneration prompt, for AI versions")
    look_name: str = Field(default="", description="The look an AI version was styled in")
    chosen: bool = Field(default=False, description="The photo the listing uses")


class CatalogueItem(VLModel):
    """One product card in the catalogue."""

    listing_id: str
    vendor_id: str
    vendor_name: str
    title: str
    sku_name: str
    category: str
    category_name: str
    unit: str
    price: Money
    currency: str
    qty: Qty
    grade: str | None = None
    status: ListingStatus
    ai_enhanced: bool = False
    image_url: str | None = None
    thumb_url: str | None = None
    flags: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class ListingEvent(VLModel):
    """One step in a listing's life, from the audit log."""

    ts: datetime
    action: str
    actor: str
    on_behalf_of: str
    outcome: str


class ProductOrder(VLModel):
    """An order that includes this product."""

    order_id: str
    store_id: str
    store_name: str
    qty: Qty
    unit: str
    price: Money
    status: str
    pickup_at: datetime | None = None


class CatalogueEntry(VLModel):
    """Everything about one product: the card, the copy, the photos, facts and their sources,
    what the agents and people decided, and the orders for it."""

    item: CatalogueItem
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    sources: dict[str, FactSource] = Field(default_factory=dict)
    flag_details: list[ListingFlag] = Field(default_factory=list)
    images: list[CatalogueImage] = Field(default_factory=list)
    history: list[ListingEvent] = Field(default_factory=list)
    orders: list[ProductOrder] = Field(default_factory=list)
