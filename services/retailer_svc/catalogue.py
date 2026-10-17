"""The retailer's catalogue: listings as products, with their photos served from the blob store,
the copy, where each fact came from, what agents and people decided (from the audit log), and the
orders placed for each product. Read-only: listings change only through the listing flow."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from vl_core.errors import Problem
from vl_core.imaging import PhotoError, mime_of, preview
from vl_core.obs import get_logger
from vl_core.schemas import (
    AuditRecord,
    CatalogueEntry,
    CatalogueImage,
    CatalogueItem,
    ImageCandidate,
    Listing,
    ListingEvent,
    ProductOrder,
    RetailerOrder,
)

if TYPE_CHECKING:
    from retailer_svc.operations import RetailerOperations

log = get_logger("catalogue")
THUMB_PX = 360
ImageSize = Literal["full", "thumb"]


class Catalogue:
    def __init__(self, ops: RetailerOperations) -> None:
        self.ops = ops

    # ---- reads ----------------------------------------------------------------------------------

    async def items(
        self,
        retailer_id: str,
        status: str | None = None,
        vendor_id: str | None = None,
        sku: str | None = None,
    ) -> list[CatalogueItem]:
        """Product cards, newest activity first. Filters: status, vendor, product name."""
        listings = await self.ops.store.listings.query()
        if status:
            listings = [x for x in listings if x.status == status]
        if vendor_id:
            listings = [x for x in listings if x.vendor_id == vendor_id]
        if sku:
            listings = [x for x in listings if x.sku_name.casefold() == sku.casefold()]
        history = await self._history(retailer_id)
        cards = [await self._item(retailer_id, x, history.get(x.id, [])) for x in listings]
        return sorted(cards, key=lambda c: _when(c.updated_at), reverse=True)

    async def entry(self, retailer_id: str, listing_id: str) -> CatalogueEntry:
        listing = await self._listing(listing_id)
        events = (await self._history(retailer_id)).get(listing.id, [])
        orders = await self.ops.store.orders.query(retailer_id=retailer_id)
        return CatalogueEntry(
            item=await self._item(retailer_id, listing, events),
            description=listing.description,
            bullets=listing.bullets,
            attributes={k: v for k, v in listing.attributes.items() if not k.startswith("photo_")},
            sources=listing.sources,
            flag_details=listing.flags,
            images=[self._image(retailer_id, listing, c) for c in self._candidates(listing)],
            history=events,
            orders=await self._orders(retailer_id, listing, orders),
        )

    async def image(
        self, retailer_id: str, listing_id: str, image_id: str, size: ImageSize
    ) -> tuple[bytes, str]:
        """The bytes of one of the listing's photos (full size, or a thumbnail)."""
        listing = await self._listing(listing_id)
        candidate = next((c for c in self._candidates(listing) if c.id == image_id), None)
        if candidate is None or not candidate.uri:
            raise Problem(404, "unknown_image", image_id)
        data = await self.ops.blob.get(candidate.uri)
        try:
            if size == "thumb":
                return await asyncio.to_thread(preview, data, THUMB_PX), "image/jpeg"
            return data, mime_of(data)
        except PhotoError as exc:
            raise Problem(422, "unreadable_image", image_id) from exc

    # ---- parts ----------------------------------------------------------------------------------

    async def _listing(self, listing_id: str) -> Listing:
        listing = await self.ops.store.listings.get(listing_id)
        if listing is None:
            raise Problem(404, "unknown_listing", listing_id)
        return listing

    async def _item(
        self, retailer_id: str, listing: Listing, events: list[ListingEvent]
    ) -> CatalogueItem:
        active = await self.ops.active(retailer_id)
        category = next(
            (c for c in active.retailer_profile.categories if c.id == listing.category), None
        )
        chosen = self._chosen(listing)
        shown = self._image(retailer_id, listing, chosen) if chosen else None
        return CatalogueItem(
            listing_id=listing.id,
            vendor_id=listing.vendor_id,
            vendor_name=await self.ops.vendor_name(listing.vendor_id),
            title=listing.title or listing.sku_name,
            sku_name=listing.sku_name,
            category=listing.category,
            category_name=category.name.get("en", category.id) if category else listing.category,
            unit=listing.unit,
            price=listing.price,
            currency=listing.currency,
            qty=listing.qty,
            grade=listing.grade,
            status=listing.status,
            ai_enhanced=listing.ai_enhanced,
            image_url=shown.url if shown else None,
            thumb_url=shown.thumb_url if shown else None,
            flags=[f.code for f in listing.flags],
            updated_at=events[-1].ts if events else None,
        )

    @staticmethod
    def _candidates(listing: Listing) -> list[ImageCandidate]:
        """The listing's photos; a listing built before photo versions existed has only its
        photo URIs."""
        if listing.images:
            return listing.images
        return [
            ImageCandidate(id="original" if n == 0 else f"photo-{n}", kind="original", uri=uri)
            for n, uri in enumerate(listing.photos)
        ]

    def _chosen(self, listing: Listing) -> ImageCandidate | None:
        candidates = self._candidates(listing)
        in_use = set(listing.photos)
        return next((c for c in candidates if c.uri and c.uri in in_use), None) or next(
            (c for c in candidates if c.uri), None
        )

    def _image(self, retailer_id: str, listing: Listing, c: ImageCandidate) -> CatalogueImage:
        base = f"/retailers/{retailer_id}/catalogue/{listing.id}/images/{c.id}"
        return CatalogueImage(
            id=c.id,
            kind=c.kind,
            url=base if c.uri else None,
            thumb_url=f"{base}?size=thumb" if c.uri else None,
            prompt=c.prompt,
            look_name=c.look_name.get("en", ""),
            chosen=bool(c.uri) and c.uri in listing.photos,
        )

    async def _history(self, retailer_id: str) -> dict[str, list[ListingEvent]]:
        """Audit records grouped by listing id (the target), oldest first. Includes what her
        side recorded (her confirmation, her Guardian's approval) for the same listing."""
        try:
            async with self.ops.gateway(retailer_id).client() as client:
                response = await client.get("/audit", params={"limit": 5000})
                response.raise_for_status()
                records = [AuditRecord.model_validate(r) for r in response.json()]
        except Exception as exc:  # the catalogue still shows products if the gateway is down
            log.warning("listing history unavailable", error=repr(exc)[:200])
            return {}
        grouped: dict[str, list[ListingEvent]] = {}
        for r in records:
            if r.action.startswith(("listing.", "queue.listing", "queue.approve", "queue.reject")):
                grouped.setdefault(r.target, []).append(
                    ListingEvent(
                        ts=r.ts,
                        action=r.action,
                        actor=r.actor_agent_id,
                        on_behalf_of=r.on_behalf_of,
                        outcome=r.outcome,
                    )
                )
        return grouped

    async def _orders(
        self, retailer_id: str, listing: Listing, orders: list[RetailerOrder]
    ) -> list[ProductOrder]:
        """Orders from this vendor that include this product (latest version of each)."""
        found = []
        for order in orders:
            if order.vendor_id != listing.vendor_id:
                continue
            latest = order.versions[-1]
            for line in latest.lines:
                if line.sku.casefold() != listing.sku_name.casefold():
                    continue
                found.append(
                    ProductOrder(
                        order_id=order.order_id,
                        store_id=order.store_id,
                        store_name=await self.ops.store_name(retailer_id, order.store_id),
                        qty=line.qty,
                        unit=line.unit,
                        price=line.price,
                        status=order.status,
                        pickup_at=latest.pickup_at,
                    )
                )
        return sorted(found, key=lambda o: _when(o.pickup_at), reverse=True)


def _when(ts: datetime | None) -> float:
    return ts.timestamp() if ts else 0.0
