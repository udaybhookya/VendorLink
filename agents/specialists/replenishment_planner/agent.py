"""order.replenish / order.create: demand + the vendor's listings → one OrderProposal per store."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from agents.base import Agent, skill
from vl_core.schemas import DemandResponse, OrderLine, OrderPlanIn, OrderPlanOut, OrderProposal


class ReplenishmentPlanner(Agent):
    async def _demand(self, inp: OrderPlanIn) -> DemandResponse:
        if inp.demand_override is not None:
            return DemandResponse(
                store_id=inp.store_id, for_date=inp.for_date, lines=inp.demand_override
            )
        async with self.ctx.http(self.ctx.settings.mocks_url) as client:
            response = await client.get(
                "/demand", params={"store": inp.store_id, "for_date": inp.for_date.isoformat()}
            )
        response.raise_for_status()
        return DemandResponse.model_validate(response.json())

    @skill("order.replenish")
    async def replenish(self, inp: OrderPlanIn) -> OrderPlanOut:
        demand = await self._demand(inp)
        listings = {
            (lst.sku_name, lst.unit): lst for lst in inp.listings if lst.status == "published"
        }
        tz = ZoneInfo(inp.country_pack.timezone)
        lines: list[OrderLine] = []
        pickups: list[datetime] = []
        skipped: list[str] = []
        unmatched = []
        for d in demand.lines:
            listing = listings.get((d.sku, d.unit))
            if listing is None:
                skipped.append(d.sku)
                unmatched.append(d)
                continue
            lines.append(OrderLine(sku=d.sku, qty=Decimal(d.qty), unit=d.unit, price=listing.price))
            pickups.append(datetime.combine(inp.for_date, time.fromisoformat(d.pickup_local), tz))
        if not lines:
            return OrderPlanOut(
                proposals=[],
                notes=f"no vendor listing for: {', '.join(skipped)}",
                unmatched=unmatched,
            )
        proposal = OrderProposal(
            order_id=f"ord-{inp.store_id.split(':')[-1]}-{inp.for_date:%m%d}-{uuid.uuid4().hex[:6]}",
            version=1,
            retailer_id=inp.retailer_id,
            vendor_id=inp.vendor_id,
            store_id=inp.store_id,
            lines=tuple(lines),
            pickup_at=min(pickups),
            payment_days=inp.payment_days,
            currency=inp.country_pack.currency,
            terms_version=inp.terms_version,
        )
        notes = f"skipped (no listing): {', '.join(skipped)}" if skipped else ""
        return OrderPlanOut(proposals=[proposal], notes=notes, unmatched=unmatched)

    @skill("order.create")
    async def create(self, inp: OrderPlanIn) -> OrderPlanOut:
        """A one-off order from explicit demand lines (same planning rules)."""
        return await self.replenish(inp)
