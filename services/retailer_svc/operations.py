"""Retailer-tenant operations: orders, approval queue, listings, vendors and signing.

This is the stateful side of the retailer. Every planning and policy decision is delegated to the
Orchestrator agent (reached by capability through the agent registry), which in turn finds
specialists by capability. Nothing here imports an agent.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, date, datetime
from typing import Any, cast

from agents.control import HostControl
from agents.registry import AgentRegistry
from agents.resources import CONTACTS, PUBLISH, RESOLVED
from retailer_svc.store import RetailerStore
from retailer_svc.studio import ConfigStudio
from vl_core.a2a import GatewayClient, agent_id_for, party_id_for
from vl_core.config import resolve_active_config
from vl_core.crypto import Signer, b64e, canonical_json, hash_obj
from vl_core.errors import Problem
from vl_core.events import Broadcaster
from vl_core.obs import get_logger
from vl_core.schemas import (
    A2AEnvelope,
    Ack,
    ActiveConfig,
    BuildListingIn,
    BuildListingOut,
    BusinessProfile,
    CatalogueStandards,
    ConfigRefs,
    CounterResponse,
    CounterReviewIn,
    CounterReviewOut,
    CountryPack,
    Decision,
    DecisionStatus,
    DirectoryEntry,
    Listing,
    ListingFlag,
    ListingReviewIn,
    ListingReviewOut,
    ListingStatusUpdate,
    ListingStepIn,
    ListingStepOut,
    ListingSubmission,
    OnboardIn,
    OnboardOut,
    OrderMandate,
    OrderMandateRequest,
    OrderPlanIn,
    OrderProposal,
    PartyConfig,
    PlanOrdersIn,
    PlanOrdersOut,
    QueueItem,
    RetailerOrder,
    RetailerOrderStatus,
    RetailerVendor,
    SignedOrder,
    TermsJobIn,
    TermsOut,
    TermsProposal,
    VendorApplication,
    VendorCheckIn,
    VendorCheckOut,
    VendorDeclined,
    VendorInvite,
    VendorStatus,
    VendorStatusUpdate,
)
from vl_core.settings import Settings
from vl_core.store import BlobStore

log = get_logger("retailer")


def _now() -> datetime:
    return datetime.now(UTC)


def original_photo(listing: Listing) -> str | None:
    """The photo she sent (the first image our agents kept as `original`)."""
    original = next((c.uri for c in listing.images if c.kind == "original" and c.uri), None)
    return original or (listing.photos[0] if listing.photos else None)


def with_photo_choice(
    stored: Listing, confirmed: Listing, standards: CatalogueStandards | None
) -> Listing:
    """Apply the photo she (or her Guardian) chose. Only photos our agents built are accepted.
    A new photo drops the old photo's flags; `RetailerOperations._recheck_photo` then checks the
    new one. An AI photo may need a person (retailer policy). Facts only the model supplied were
    confirmed by her (her Guardian never approves those)."""
    offered = {c.uri for c in stored.images if c.uri} | set(stored.photos)
    if not set(confirmed.photos) <= offered:
        raise Problem(409, "photo_not_offered", "choose her photo or one of the versions offered")
    chosen = confirmed.photos[0] if confirmed.photos else original_photo(stored)
    new_photo = chosen is not None and chosen != original_photo(stored)
    generated = {c.uri for c in stored.images if c.kind == "generated" and c.uri}
    ai_photo = chosen in generated
    flags = [
        f
        for f in stored.flags
        if not f.code.startswith("FACT_NOT_FROM_VENDOR_")
        and not (new_photo and f.code.startswith("IMAGE_"))
    ]
    if ai_photo and standards is not None and standards.image.ai_images_need_review:
        flags.append(ListingFlag(code="AI_PHOTO_NEEDS_REVIEW", detail="AI-enhanced photo"))
    sources = {
        k: ("vendor" if v == "model" else v) for k, v in stored.sources.items()
    }  # she confirmed them
    return stored.model_copy(
        update={
            "photos": [chosen] if chosen else stored.photos,
            "ai_enhanced": ai_photo,
            "flags": flags,
            "sources": sources,
        }
    )


class RetailerOperations:
    def __init__(
        self,
        settings: Settings,
        signer: Signer,
        store: RetailerStore,
        studio: ConfigStudio,
        blob: BlobStore,
        agents: AgentRegistry | None = None,
    ) -> None:
        self.settings = settings
        self._signer = signer  # the retailer's own agent key: gateway requests and order mandates
        self.store = store
        self.studio = studio
        self.blob = blob
        self.control = HostControl(
            settings, "retailer-svc", signer, owns_key=lambda aid: aid.startswith("retailer-agent:")
        )
        self.agents = agents or AgentRegistry.load(
            settings, host="retailer-svc", control=self.control
        )
        self.bus = Broadcaster()
        from retailer_svc.hitl import RetailerHumanLoop
        from retailer_svc.jobs import JobRunner

        self.hitl = RetailerHumanLoop(self)
        self.jobs = JobRunner(self)
        self.agents.resources.update(
            {CONTACTS: self.hitl.contacts, RESOLVED: self.hitl.resolved, PUBLISH: self.hitl.publish}
        )

    # ---- identity, config, audit -------------------------------------------------------------

    def gateway(self, retailer_id: str) -> GatewayClient:
        return GatewayClient(
            self.settings.trust_gateway_url, agent_id_for(retailer_id), self._signer
        )

    async def active(self, retailer_id: str) -> ActiveConfig:
        active = await self.store.active.get(retailer_id)
        if active is None:
            raise Problem(409, "no_active_config", retailer_id)
        return active

    async def activate(
        self, retailer_id: str, refs: ConfigRefs, preset: str | None
    ) -> ActiveConfig:
        active = resolve_active_config(retailer_id, refs, self.studio)
        await self.store.active.put(retailer_id, active)
        await self.gateway(retailer_id).set_party_config(
            PartyConfig(retailer_id=retailer_id, config_hash=active.config_hash, preset=preset)
        )
        log.info(
            f"config activated{f' (preset {preset})' if preset else ''}",
            business=active.business_profile.id,
            country=active.country_pack.code,
            specialists=",".join(active.specialists),
            config_hash=active.config_hash[:12],
        )
        self._publish(
            "config",
            {
                "retailer_id": retailer_id,
                "config_hash": active.config_hash,
                "preset": preset,
                "specialists": active.specialists,
            },
        )
        return active

    async def _audit(self, retailer_id: str, action: str, target: str, payload: Any) -> None:
        await self.gateway(retailer_id).audit(action, target, payload, retailer_id=retailer_id)
        log.info(f"recorded {action}", target=target, on_behalf_of=retailer_id)

    def _publish(self, event: str, data: Any) -> None:
        self.bus.publish(
            event, data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        )

    def _profiles_for(
        self, business_profile: str, country: str
    ) -> tuple[BusinessProfile, CountryPack]:
        bp = cast(BusinessProfile, self.studio.load("business_profiles", business_profile))
        cp = cast(CountryPack, self.studio.load("country_packs", country))
        return bp, cp

    async def _queue(
        self, retailer_id: str, kind: str, ref_id: str, reason: str, params: dict[str, Any]
    ) -> QueueItem:
        item = QueueItem.model_validate(
            {
                "id": f"q-{uuid.uuid4().hex[:8]}",
                "retailer_id": retailer_id,
                "kind": kind,
                "ref_id": ref_id,
                "reason": reason,
                "created_at": _now(),
            }
        )
        task = await self.hitl.ask_approval(retailer_id, kind, ref_id, item.id, params)
        item = item.model_copy(update={"task_id": task.id})
        await self.store.queue.put(item.id, item)
        await self._audit(retailer_id, f"queue.{kind}", ref_id, item)
        self._publish("queue", item)
        return item

    async def limit_text(self, retailer_id: str) -> str:
        active = await self.active(retailer_id)
        return f"{active.country_pack.currency} {active.retailer_policy.auto_order_max_value}"

    async def vendor_name(self, vendor_id: str) -> str:
        record = await self.store.vendors.get(vendor_id)
        return record.display_name if record else vendor_id

    async def store_name(self, retailer_id: str, store_id: str) -> str:
        retailer = await self.store.retailers.get(retailer_id)
        store = next((s for s in (retailer.stores if retailer else []) if s.id == store_id), None)
        return store.name if store else store_id

    async def _vendor_record(self, vendor_id: str) -> RetailerVendor:
        record = await self.store.vendors.get(vendor_id)
        if record is None:
            raise Problem(403, "unknown_vendor", "vendor has not applied to this retailer")
        return record

    # ---- A2A: vendor → retailer ---------------------------------------------------------------

    async def on_vendor_onboard(self, env: A2AEnvelope) -> VendorCheckOut:
        app = VendorApplication.model_validate(env.body)
        retailer_id = party_id_for(env.to_agent)
        active = await self.active(retailer_id)
        bp, cp = self._profiles_for(app.vendor.business_profile, app.vendor.country)
        check_in = VendorCheckIn(
            vendor=app.vendor,
            legal_name=app.legal_name,
            doc_numbers=app.doc_numbers,
            business_profile=bp,
            country_pack=cp,
            listings=await self.store.listings.query(vendor_id=app.vendor.id),
        )
        out = await self.agents.call(
            "orchestrator.onboard_vendor",
            OnboardIn(active_config=active, check_in=check_in),
            OnboardOut,
        )
        record = RetailerVendor(
            vendor_id=app.vendor.id,
            display_name=app.vendor.display_name,
            country=app.vendor.country,
            business_profile=app.vendor.business_profile,
            status=out.status,
            checks=out.result.checks,
            updated_at=_now(),
        )
        await self.store.vendors.put(record.vendor_id, record)
        await self._audit(retailer_id, "vendor.checked", app.vendor.id, {"result": out})
        if out.status == "pending_approval":
            checks = ", ".join(f"{c.doc_type} {c.code}" for c in out.result.checks) or "none"
            await self._queue(
                retailer_id,
                "vendor",
                app.vendor.id,
                "new vendor needs approval",
                {"vendor": app.vendor.display_name, "checks": checks},
            )
        self._publish("vendor", record)
        return out.result

    async def build_listing(
        self, retailer_id: str, sub: ListingSubmission, vendor: RetailerVendor
    ) -> BuildListingOut:
        active = await self.active(retailer_id)
        bp, cp = self._profiles_for(vendor.business_profile, vendor.country)
        photo_uri = None
        if sub.photo_b64:
            photo_uri = await self.blob.put(
                f"listings/{sub.vendor_id}/{uuid.uuid4().hex}",
                base64.b64decode(sub.photo_b64),
                sub.photo_mime,
            )
        step_in = ListingStepIn(
            vendor_id=sub.vendor_id,
            language=sub.language,
            photo_uri=photo_uri,
            transcript=sub.transcript,
            label_text=sub.label_text,
            answers=sub.answers,
            business_profile=bp,
            retailer_profile=active.retailer_profile,
            country_pack=cp,
        )
        return await self.agents.call(
            "orchestrator.build_listing",
            BuildListingIn(active_config=active, step_in=step_in),
            BuildListingOut,
        )

    async def on_listing_submit(self, env: A2AEnvelope) -> ListingStepOut:
        sub = ListingSubmission.model_validate(env.body)
        retailer_id = party_id_for(env.to_agent)
        vendor = await self._vendor_record(sub.vendor_id)
        built = await self.build_listing(retailer_id, sub, vendor)
        out = built.result
        if out.listing is not None:
            status = "needs_review" if out.needs_review else "draft"
            await self.store.listings.put(
                out.listing.id, out.listing.model_copy(update={"status": status})
            )
        await self._audit(
            retailer_id,
            "listing.built",
            out.listing.id if out.listing else "-",
            {"result": out, "specialists": built.ran},
        )
        self._publish(
            "listing",
            {
                "vendor_id": sub.vendor_id,
                "specialists": built.ran,
                "result": out.model_dump(mode="json"),
            },
        )
        return out

    async def _tell_vendor(self, retailer_id: str, listing: Listing) -> None:
        """A person decided on her listing later (review queue): her side keeps its copy true."""
        await self.gateway(retailer_id).relay(
            agent_id_for(listing.vendor_id),
            "listing.status",
            ListingStatusUpdate(
                listing_id=listing.id,
                vendor_id=listing.vendor_id,
                retailer_id=retailer_id,
                status=listing.status,
                reasons=[f.code for f in listing.flags] if listing.status != "published" else [],
                ai_enhanced=listing.ai_enhanced,
            ),
            on_behalf_of=retailer_id,
            response_model=Ack,
        )

    async def _recheck_photo(
        self, listing: Listing, vendor: RetailerVendor, active: ActiveConfig
    ) -> Listing:
        """She chose a version of her photo: check that photo against our standards with the
        same agent that checked the original. Never assume the new photo fixed everything."""
        chosen = listing.photos[0] if listing.photos else None
        if chosen is None or chosen == original_photo(listing):
            return listing
        if not self.agents.find_all("image.assess", active):
            return listing  # no catalogue checks in this configuration
        bp, cp = self._profiles_for(vendor.business_profile, vendor.country)
        out = await self.agents.call(
            "image.assess",
            ListingStepIn(
                vendor_id=listing.vendor_id,
                language="en",  # a retailer-side check; no text for her
                photo_uri=chosen,
                listing=listing,
                business_profile=bp,
                retailer_profile=active.retailer_profile,
                country_pack=cp,
            ),
            ListingStepOut,
            active,
        )
        rechecked = out.listing or listing
        log.info(
            "chosen photo rechecked",
            listing_id=listing.id,
            flags=",".join(f.code for f in out.flags) or None,
        )
        return rechecked

    async def on_listing_confirm(self, env: A2AEnvelope) -> Ack:
        confirmed = Listing.model_validate(env.body)
        retailer_id = party_id_for(env.to_agent)
        active = await self.active(retailer_id)
        stored = await self.store.listings.get(confirmed.id)
        if stored is None or stored.vendor_id != confirmed.vendor_id:
            raise Problem(404, "unknown_listing", confirmed.id)
        fields = {
            "sku_name",
            "category",
            "unit",
            "price",
            "qty",
            "grade",
            "flags",
            "title",
            "description",
            "bullets",
            "attributes",
        }
        if hash_obj(stored.model_dump(include=fields)) != hash_obj(
            confirmed.model_dump(include=fields)
        ):
            raise Problem(409, "listing_changed", "confirm the draft exactly as built")
        stored = with_photo_choice(stored, confirmed, active.retailer_profile.catalogue_standards)
        vendor = await self._vendor_record(stored.vendor_id)
        stored = await self._recheck_photo(stored, vendor, active)
        review = await self.agents.call(
            "orchestrator.review_listing",
            ListingReviewIn(
                active_config=active, listing=stored, vendor_approved=vendor.status == "approved"
            ),
            ListingReviewOut,
        )
        listing = stored.model_copy(
            update={"status": "published" if review.publish else "needs_review"}
        )
        await self.store.listings.put(listing.id, listing)
        action = "listing.publish" if review.publish else "listing.review"
        await self._audit(retailer_id, action, listing.id, listing)
        if not review.publish:
            await self._queue(
                retailer_id,
                "listing",
                listing.id,
                ", ".join(review.reasons),
                {
                    "sku": listing.sku_name,
                    "vendor": vendor.display_name,
                    "reasons": ", ".join(review.reasons),
                },
            )
        self._publish("listing", listing)
        return Ack(ok=review.publish, detail="published" if review.publish else "sent to review")

    async def on_vendor_signed(self, env: A2AEnvelope) -> Ack:
        signed = SignedOrder.model_validate(env.body)
        order = await self._order(signed.proposal.order_id)
        if hash_obj(signed.proposal) != hash_obj(order.latest):
            raise Problem(409, "not_latest_version", "vendor signed a different version")
        await self._finalize(order, signed.vendor_sig, signed.consent_ref)
        return Ack(detail="order confirmed")

    # ---- orders ------------------------------------------------------------------------------

    async def _order(self, order_id: str) -> RetailerOrder:
        order = await self.store.orders.get(order_id)
        if order is None:
            raise Problem(404, "unknown_order", order_id)
        return order

    async def _save(
        self, order: RetailerOrder, status: RetailerOrderStatus, **changes: Any
    ) -> RetailerOrder:
        updated = order.model_copy(update={"status": status, "updated_at": _now(), **changes})
        await self.store.orders.put(order.order_id, updated)
        log.info(
            f"order {status}",
            order_id=order.order_id,
            version=updated.latest.version,
            reason=updated.queue_reason or None,
        )
        self._publish("order", updated)
        return updated

    async def plan_for_store(
        self, retailer_id: str, vendor_id: str, store_id: str, for_date: date
    ) -> PlanOrdersOut:
        active = await self.active(retailer_id)
        vendor = await self._vendor_record(vendor_id)
        if vendor.status != "approved":
            raise Problem(409, "vendor_not_approved", vendor.status)
        template = active.retailer_profile.term_templates[0]
        plan_in = OrderPlanIn(
            retailer_id=retailer_id,
            vendor_id=vendor_id,
            store_id=store_id,
            for_date=for_date,
            listings=await self.store.listings.query(vendor_id=vendor_id, status="published"),
            country_pack=active.country_pack,
            terms_version=template.id,
            payment_days=template.payment_days,
        )
        return await self.agents.call(
            "orchestrator.plan_orders",
            PlanOrdersIn(active_config=active, plan_in=plan_in),
            PlanOrdersOut,
        )

    async def create_order(
        self, retailer_id: str, proposal: OrderProposal, *, send: bool, reason: str = ""
    ) -> RetailerOrder:
        order = RetailerOrder(
            order_id=proposal.order_id,
            retailer_id=retailer_id,
            vendor_id=proposal.vendor_id,
            store_id=proposal.store_id,
            versions=[proposal],
            status="proposed",
            updated_at=_now(),
        )
        await self.store.orders.put(order.order_id, order)
        await self._audit(retailer_id, "order.create", order.order_id, proposal)
        if send:
            return await self._send(order)
        return await self._save(order, "rejected", queue_reason=reason)

    async def place_order(
        self, retailer_id: str, vendor_id: str, store_id: str, for_date: date
    ) -> list[RetailerOrder]:
        planned = await self.plan_for_store(retailer_id, vendor_id, store_id, for_date)
        gates = {g.order_id: g for g in planned.gates}
        orders = []
        for proposal in planned.plan.proposals:
            gate = gates[proposal.order_id]
            if gate.auto:
                orders.append(await self.create_order(retailer_id, proposal, send=True))
                continue
            order = RetailerOrder(
                order_id=proposal.order_id,
                retailer_id=retailer_id,
                vendor_id=vendor_id,
                store_id=store_id,
                versions=[proposal],
                status="queued",
                queue_reason=gate.reason,
                updated_at=_now(),
            )
            await self.store.orders.put(order.order_id, order)
            await self._audit(retailer_id, "order.create", order.order_id, proposal)
            await self._queue(
                retailer_id,
                "place_order",
                order.order_id,
                gate.reason,
                {
                    "order": order.order_id,
                    "store": await self.store_name(retailer_id, store_id),
                    "value": f"{proposal.currency} {proposal.total_value()}",
                    "limit": await self.limit_text(retailer_id),
                    "vendor": await self.vendor_name(vendor_id),
                },
            )
            self._publish("order", order)
            orders.append(order)
        return orders

    async def _send(self, order: RetailerOrder) -> RetailerOrder:
        decision = await self.gateway(order.retailer_id).relay(
            agent_id_for(order.vendor_id),
            "order.propose",
            order.latest,
            on_behalf_of=order.retailer_id,
            response_model=Decision,
        )
        return await self._on_decision(order, decision)

    async def _on_decision(self, order: RetailerOrder, decision: Decision) -> RetailerOrder:
        log.info(
            f"vendor's Guardian answered {decision.status.value}",
            order_id=order.order_id,
            version=decision.version,
            reasons=",".join(r.code for r in decision.reasons) or None,
            counter_version=decision.counter.version if decision.counter else None,
            signed=bool(decision.vendor_sig) or None,
        )
        order = order.model_copy(update={"decisions": [*order.decisions, decision]})
        if decision.vendor_sig:
            return await self._finalize(order, decision.vendor_sig, None)
        if decision.status == DecisionStatus.RED and decision.counter is not None:
            order = await self._save(
                order, "countered", versions=[*order.versions, decision.counter]
            )
            return await self.handle_counter(order)
        if decision.status == DecisionStatus.AMBER:
            return await self._save(order, "awaiting_vendor")
        return await self._save(order, "declined")

    async def handle_counter(self, order: RetailerOrder) -> RetailerOrder:
        active = await self.active(order.retailer_id)
        review = await self.agents.call(
            "orchestrator.review_counter",
            CounterReviewIn(
                active_config=active, original=order.versions[-2], counter=order.versions[-1]
            ),
            CounterReviewOut,
        )
        log.info(
            "counter " + ("accepted by policy" if review.accept else "needs the buyer"),
            order_id=order.order_id,
            delta=f"{review.delta:.0%}",
            reason=review.reason or None,
        )
        if review.accept:
            return await self._answer_counter(order, accept=True)
        order = await self._save(order, "queued", queue_reason=review.reason)
        counter = order.versions[-1]
        summary = ", ".join(f"{line.qty} {line.unit} {line.sku}" for line in counter.lines)
        await self._queue(
            order.retailer_id,
            "handle_counter",
            order.order_id,
            review.reason,
            {
                "vendor": await self.vendor_name(order.vendor_id),
                "order": order.order_id,
                "summary": summary,
                "delta": f"{review.delta:.0%}",
            },
        )
        return order

    async def _answer_counter(self, order: RetailerOrder, *, accept: bool) -> RetailerOrder:
        decision = await self.gateway(order.retailer_id).relay(
            agent_id_for(order.vendor_id),
            "order.counter_response",
            CounterResponse(order_id=order.order_id, accept=accept, proposal=order.latest),
            on_behalf_of=order.retailer_id,
            response_model=Decision,
        )
        if not accept:
            return await self._save(order, "rejected", decisions=[*order.decisions, decision])
        return await self._on_decision(order, decision)

    async def _finalize(
        self, order: RetailerOrder, vendor_sig: str, consent_ref: str | None
    ) -> RetailerOrder:
        proposal: OrderProposal = order.latest
        retailer_sig = b64e(
            await self._signer.sign(agent_id_for(order.retailer_id), canonical_json(proposal))
        )
        mandate = OrderMandate(
            order_id=proposal.order_id,
            final_version=proposal.version,
            terms_hash=hash_obj(proposal),
            vendor_sig=vendor_sig,
            retailer_sig=retailer_sig,
            consent_ref=consent_ref,
            created_at=_now(),
        )
        await self.gateway(order.retailer_id).post_order_mandate(
            OrderMandateRequest(proposal=proposal, mandate=mandate)
        )
        return await self._save(order, "confirmed", mandate_terms_hash=mandate.terms_hash)

    async def propose_terms(self, retailer_id: str, vendor_id: str) -> tuple[TermsProposal, Ack]:
        active = await self.active(retailer_id)
        out = await self.agents.call(
            "orchestrator.propose_terms",
            TermsJobIn(active_config=active, retailer_id=retailer_id, vendor_id=vendor_id),
            TermsOut,
        )
        ack = await self.gateway(retailer_id).relay(
            agent_id_for(vendor_id),
            "terms.propose",
            out.terms,
            on_behalf_of=retailer_id,
            response_model=Ack,
        )
        return out.terms, ack

    # ---- approval queue ------------------------------------------------------------------------

    async def resolve(
        self, retailer_id: str, item_id: str, approve: bool, *, from_task: bool = False
    ) -> QueueItem:
        item = await self.store.queue.get(item_id)
        if item is None or item.retailer_id != retailer_id:
            raise Problem(404, "unknown_queue_item", item_id)
        if item.status != "pending":
            raise Problem(409, "already_resolved", item.status)
        if item.task_id and not from_task:  # decided in the console: stop asking on Slack/email
            await self.hitl.close(item.task_id, "resolved in the console")
        if item.kind == "place_order":
            order = await self._order(item.ref_id)
            await (self._send(order) if approve else self._save(order, "rejected"))
        elif item.kind == "handle_counter":
            await self._answer_counter(await self._order(item.ref_id), accept=approve)
        elif item.kind == "vendor":
            record = await self._vendor_record(item.ref_id)
            record = record.model_copy(
                update={"status": "approved" if approve else "rejected", "updated_at": _now()}
            )
            await self.store.vendors.put(record.vendor_id, record)
            await self.gateway(retailer_id).relay(
                agent_id_for(record.vendor_id),
                "vendor.status",
                VendorStatusUpdate(
                    vendor_id=record.vendor_id,
                    status=VendorStatus.ACTIVE if approve else VendorStatus.DRAFT,
                ),
                on_behalf_of=retailer_id,
                response_model=Ack,
            )
            self._publish("vendor", record)
        elif item.kind == "listing":
            listing = await self.store.listings.get(item.ref_id)
            if listing is not None:
                listing = listing.model_copy(
                    update={"status": "published" if approve else "rejected"}
                )
                await self.store.listings.put(listing.id, listing)
                await self._tell_vendor(retailer_id, listing)
                self._publish("listing", listing)
        resolved = item.model_copy(update={"status": "approved" if approve else "rejected"})
        await self.store.queue.put(item.id, resolved)
        await self._audit(
            retailer_id, f"queue.{'approve' if approve else 'reject'}", item.ref_id, resolved
        )
        self._publish("queue", resolved)
        return resolved

    # ---- supplier network ------------------------------------------------------------------------

    async def directory(self, retailer_id: str) -> list[DirectoryEntry]:
        """Suppliers registered on the platform, with their status at this retailer."""
        async with self.gateway(retailer_id).client() as client:
            response = await client.get("/agents")
            response.raise_for_status()
        connected = {v.vendor_id: v.status for v in await self.store.vendors.query()}
        return [
            DirectoryEntry(
                party_id=a["on_behalf_of"],
                party_type="vendor",
                display_name=a.get("display_name") or a["on_behalf_of"],
                agent_id=a["agent_id"],
                status=connected.get(a["on_behalf_of"], "not_connected"),
            )
            for a in response.json()
            if a.get("kind", "party") == "party" and a["party_type"] == "vendor"
        ]

    async def invite(self, retailer_id: str, vendor_id: str, message: str) -> VendorInvite:
        retailer = await self.store.retailers.get(retailer_id)
        invite = VendorInvite(
            invite_id=f"inv-{uuid.uuid4().hex[:8]}",
            retailer_id=retailer_id,
            vendor_id=vendor_id,
            retailer_name=retailer.display_name if retailer else retailer_id,
            message=message,
            sent_at=_now(),
        )
        await self.gateway(retailer_id).relay(
            agent_id_for(vendor_id),
            "vendor.invite",
            invite,
            on_behalf_of=retailer_id,
            response_model=Ack,
        )
        await self._audit(retailer_id, "vendor.invite", vendor_id, invite)
        self._publish("vendor", {"invited": vendor_id})
        return invite

    async def on_vendor_declined(self, env: A2AEnvelope) -> Ack:
        declined = VendorDeclined.model_validate(env.body)
        order = await self._order(declined.order_id)
        if order.vendor_id != declined.vendor_id or order.latest.version != declined.version:
            raise Problem(409, "not_latest_version", "decline names a different version")
        await self._save(order, "declined", queue_reason="vendor said no")
        await self._audit(order.retailer_id, "order.vendor_declined", order.order_id, declined)
        return Ack(detail="order closed")
