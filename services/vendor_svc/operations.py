"""Vendor-side flows. API routes, A2A handlers and the Concierge (via its port) call into this.

Nothing here can sign: every signature comes from the Guardian agent, reached by capability
through the agent registry (`guardian.decide`, `guardian.sign_order`, `guardian.sign_mandate`).
The Guardian re-checks the mandate and the consent itself.
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from agents.control import HostControl
from agents.registry import AgentRegistry
from agents.resources import CONTACTS, PUBLISH, RESOLVED
from vendor_svc.store import VendorStore
from vl_core.a2a import GatewayClient, agent_id_for, make_client, party_id_for, register_credential
from vl_core.config import ConfigLoader
from vl_core.crypto import Signer, hash_obj
from vl_core.errors import Problem
from vl_core.events import Broadcaster
from vl_core.i18n import Strings, is_affirmative
from vl_core.obs import get_logger
from vl_core.readback import (
    deal_readback,
    explain,
    listing_readback,
    party_name,
    rules_readback,
)
from vl_core.schemas import (
    FALLBACK_LANGUAGE,
    A2AEnvelope,
    Ack,
    AuditRecord,
    AutoAccept,
    BusinessOption,
    BusinessProfile,
    ConsentRecord,
    ConsentRequest,
    CounterResponse,
    CountryOption,
    CountryPack,
    CredentialRequest,
    Deal,
    DealStatus,
    DealView,
    DecideIn,
    Decision,
    DecisionStatus,
    DirectoryEntry,
    Listing,
    ListingApprovalIn,
    ListingApprovalOut,
    ListingDraftView,
    ListingStatusUpdate,
    ListingStepOut,
    ListingSubmission,
    MandateOut,
    MandateQuery,
    OrderProposal,
    ProfileUpdate,
    RequiredDocs,
    RulesDraft,
    RulesView,
    ScreenRequest,
    SignedOrder,
    SignMandateIn,
    SignOrderIn,
    SignOrderOut,
    SkuRule,
    StandingMandate,
    TermsProposal,
    TermsResponse,
    Vendor,
    VendorApplication,
    VendorCheckOut,
    VendorDeclined,
    VendorDoc,
    VendorInvite,
    VendorOptions,
    VendorRegistration,
    VendorStatus,
    VendorStatusUpdate,
    VendorTerms,
)
from vl_core.settings import Settings
from vl_core.store import BlobStore, get_blob_store

log = get_logger("vendor")

STATUS_FOR: dict[DecisionStatus, DealStatus] = {
    DecisionStatus.GREEN: "signed",
    DecisionStatus.AMBER: "awaiting_consent",
    DecisionStatus.RED: "countered",
    DecisionStatus.DECLINE: "declined",
}


class VendorOperations:
    def __init__(
        self,
        settings: Settings,
        signer: Signer,
        *,
        blob: BlobStore | None = None,
        loader: ConfigLoader | None = None,
        agents: AgentRegistry | None = None,
    ) -> None:
        self.settings = settings
        self._signer = signer  # for the gateway client; the registry gives it only to the Guardian
        self.store = VendorStore.create(settings)
        self.control = HostControl(
            settings, "vendor-svc", signer, owns_key=lambda aid: aid.startswith("vendor-agent:")
        )
        self.agents = agents or AgentRegistry.load(
            settings, host="vendor-svc", signer=signer, control=self.control
        )
        from vendor_svc.hitl import VendorHumanLoop

        self.hitl = VendorHumanLoop(self)
        self.agents.resources.update(
            {
                CONTACTS: self.hitl.contacts,
                RESOLVED: self.hitl.resolved,
                PUBLISH: self.hitl.publish,
            }
        )
        self.blob = blob or get_blob_store(settings)
        self.loader = loader or ConfigLoader(settings.vl_config_dir)
        self.bus = Broadcaster()

    # ---- helpers ----------------------------------------------------------------------------

    def gateway(self, vendor_id: str) -> GatewayClient:
        return GatewayClient(self.settings.trust_gateway_url, agent_id_for(vendor_id), self._signer)

    async def vendor(self, vendor_id: str) -> Vendor:
        vendor = await self.store.vendors.get(vendor_id)
        if vendor is None:
            raise Problem(404, "unknown_vendor", vendor_id)
        return vendor

    def country(self, vendor: Vendor) -> CountryPack:
        return cast(CountryPack, self.loader.load("country_packs", vendor.country))

    def strings(self, vendor: Vendor, language: str | None = None) -> Strings:
        lang = language or vendor.language
        if lang not in self.country(vendor).languages and lang != FALLBACK_LANGUAGE:
            raise Problem(422, "unsupported_language", lang)
        return Strings(lang, self.settings.vl_config_dir / "i18n")

    async def _audit(
        self, vendor_id: str, action: str, target: str, payload: Any, retailer_id: str | None = None
    ) -> None:
        await self.gateway(vendor_id).audit(action, target, payload, retailer_id=retailer_id)
        log.info(f"recorded {action}", target=target, on_behalf_of=vendor_id)

    def _publish(self, event: str, vendor_id: str, data: Any) -> None:
        payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        self.bus.publish(event, {"vendor_id": vendor_id, "data": payload})

    async def mandate(self, vendor_id: str) -> StandingMandate | None:
        """The vendor's rules in force, as kept by the Guardian."""
        out = await self.agents.call(
            "guardian.mandate", MandateQuery(vendor_id=vendor_id), MandateOut
        )
        return out.mandate

    # ---- vendors ----------------------------------------------------------------------------

    async def register_vendor(self, vendor: Vendor) -> Vendor:
        """Create the vendor and its agent identity at the gateway (vendor-tenant facilitator)."""
        vendor = vendor.model_copy(update={"agent_id": agent_id_for(vendor.id)})
        await self.store.vendors.put(vendor.id, vendor)
        await register_credential(
            self.settings.trust_gateway_url,
            self.settings.vl_admin_token,
            CredentialRequest(
                agent_id=vendor.agent_id or "",
                party_type="vendor",
                on_behalf_of=vendor.id,
                display_name=vendor.display_name,
                public_key=await self.gateway(vendor.id).public_key_b64(),
                endpoint=self.settings.vendor_svc_url,
            ),
        )
        return vendor

    async def owns_agent(self, agent_id: str) -> bool:
        try:
            return await self.store.vendors.get(party_id_for(agent_id)) is not None
        except ValueError:
            return False

    # ---- deals ------------------------------------------------------------------------------

    async def deal(self, order_id: str, vendor_id: str) -> Deal:
        deal = await self.store.deals.get(order_id)
        if deal is None or deal.vendor_id != vendor_id:
            raise Problem(404, "unknown_deal", order_id)
        return deal

    async def deal_view(self, deal: Deal, language: str | None = None) -> DealView:
        vendor = await self.vendor(deal.vendor_id)
        s = self.strings(vendor, language)
        text = deal_readback(
            deal.latest, s, self.country(vendor).timezone, party_name(deal.retailer_id)
        )
        if deal.decisions:
            text = f"{text} {explain(deal.decisions[-1], s)}"
        return DealView(deal=deal, subject_hash=hash_obj(deal.latest), readback=text)

    async def deals(self, vendor_id: str) -> list[Deal]:
        return sorted(
            await self.store.deals.query(vendor_id=vendor_id),
            key=lambda d: d.updated_at,
            reverse=True,
        )

    async def _decide_and_record(self, deal: Deal, proposal: OrderProposal) -> Decision:
        """Ask the Guardian (it signs when green); store the deal; one audit per change."""
        decision = await self.agents.call("guardian.decide", DecideIn(proposal=proposal), Decision)
        versions = list(deal.versions)
        if decision.counter is not None:
            versions.append(decision.counter)
        updated = deal.model_copy(
            update={
                "versions": versions,
                "decisions": [*deal.decisions, decision],
                "status": STATUS_FOR[decision.status],
                "vendor_sig": decision.vendor_sig,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.deals.put(deal.order_id, updated)
        lines = ", ".join(f"{x.qty} {x.unit} {x.sku}" for x in proposal.lines)
        log.info(
            f"deal {deal.order_id} v{proposal.version} ({lines}): Guardian says "
            f"{decision.status.value} → {updated.status}",
            reasons=",".join(r.code for r in decision.reasons) or None,
            counter=", ".join(f"{x.qty} {x.unit} {x.sku}" for x in decision.counter.lines)
            if decision.counter
            else None,
        )
        if updated.status == "awaiting_consent":
            await self.hitl.ask_consent(updated)
        action = "guardian.sign_order" if decision.vendor_sig else "guardian.decide"
        await self._audit(deal.vendor_id, action, deal.order_id, decision, deal.retailer_id)
        self._publish("deal", deal.vendor_id, await self.deal_view(updated))
        return decision

    async def on_order_propose(self, env: A2AEnvelope) -> Decision:
        proposal = OrderProposal.model_validate(env.body)
        vendor_id = party_id_for(env.to_agent)
        if proposal.vendor_id != vendor_id:
            raise Problem(400, "wrong_vendor", proposal.vendor_id)
        existing = await self.store.deals.get(proposal.order_id)
        if existing is not None:
            raise Problem(409, "order_exists", "a new proposal needs a new order id")
        deal = Deal(
            order_id=proposal.order_id,
            retailer_id=proposal.retailer_id,
            vendor_id=vendor_id,
            store_id=proposal.store_id,
            versions=[proposal],
            status="open",
            updated_at=datetime.now(UTC),
        )
        return await self._decide_and_record(deal, proposal)

    async def on_counter_response(self, env: A2AEnvelope) -> Decision:
        response = CounterResponse.model_validate(env.body)
        deal = await self.deal(response.order_id, party_id_for(env.to_agent))
        if deal.status != "countered" or hash_obj(response.proposal) != hash_obj(deal.latest):
            raise Problem(409, "not_our_counter", "answer must name the exact counter we sent")
        if not response.accept:
            rejected = deal.model_copy(
                update={"status": "rejected", "updated_at": datetime.now(UTC)}
            )
            await self.store.deals.put(deal.order_id, rejected)
            await self._audit(
                deal.vendor_id,
                "deal.rejected_by_retailer",
                deal.order_id,
                response,
                deal.retailer_id,
            )
            self._publish("deal", deal.vendor_id, await self.deal_view(rejected))
            return Decision(
                order_id=deal.order_id, version=deal.latest.version, status=DecisionStatus.DECLINE
            )
        return await self._decide_and_record(deal, deal.latest)

    async def consent_to_deal(self, vendor_id: str, order_id: str, req: ConsentRequest) -> DealView:
        deal = await self.deal(order_id, vendor_id)
        if deal.status != "awaiting_consent":
            raise Problem(409, "nothing_to_confirm", deal.status)
        if hash_obj(deal.latest) != req.subject_hash:
            raise Problem(409, "terms_changed", "read the latest terms back before consenting")
        consent = await self._capture_consent(vendor_id, deal.latest, req)
        signed_out = await self.agents.call(
            "guardian.sign_order", SignOrderIn(proposal=deal.latest, consent=consent), SignOrderOut
        )
        sig = signed_out.vendor_sig
        signed = deal.model_copy(
            update={
                "status": "signed",
                "vendor_sig": sig,
                "consent_ref": consent.id,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.deals.put(order_id, signed)
        await self.hitl.close(order_id, "answered", "signed in the app")
        await self._audit(
            vendor_id,
            "guardian.sign_order",
            order_id,
            {"order": deal.latest, "consent": consent.id},
            deal.retailer_id,
        )
        await self.gateway(vendor_id).relay(
            agent_id_for(deal.retailer_id),
            "order.vendor_signed",
            SignedOrder(proposal=deal.latest, vendor_sig=sig, consent_ref=consent.id),
            on_behalf_of=vendor_id,
            response_model=Ack,
        )
        view = await self.deal_view(signed, req.language)
        self._publish("deal", vendor_id, view)
        return view

    async def decline_deal(self, vendor_id: str, order_id: str) -> DealView:
        deal = await self.deal(order_id, vendor_id)
        if deal.status != "awaiting_consent":
            raise Problem(409, "nothing_to_decline", deal.status)
        declined = deal.model_copy(update={"status": "declined", "updated_at": datetime.now(UTC)})
        await self.store.deals.put(order_id, declined)
        await self.hitl.close(order_id, "answered", "declined")
        await self._audit(
            vendor_id, "deal.declined_by_vendor", order_id, deal.latest, deal.retailer_id
        )
        # Tell the retailer, so the order does not wait for a yes that will not come.
        await self.gateway(vendor_id).relay(
            agent_id_for(deal.retailer_id),
            "order.vendor_declined",
            VendorDeclined(
                order_id=order_id,
                version=deal.latest.version,
                vendor_id=vendor_id,
                retailer_id=deal.retailer_id,
            ),
            on_behalf_of=vendor_id,
            response_model=Ack,
        )
        view = await self.deal_view(declined)
        self._publish("deal", vendor_id, view)
        return view

    async def _terms_verdict(self, terms: TermsProposal) -> str:
        mandate = await self.mandate(terms.vendor_id)
        if mandate is None:
            return "NO_STANDING_MANDATE"
        if terms.payment_days > mandate.payment_days_max:
            return "PAYMENT_TOO_LATE"
        return "WITHIN_RULES"

    async def on_terms(self, env: A2AEnvelope) -> Ack:
        """Terms arrive; she decides in the app. The Guardian's verdict is shown alongside."""
        terms = TermsProposal.model_validate(env.body)
        await self.vendor(terms.vendor_id)
        verdict = await self._terms_verdict(terms)
        await self.store.terms.put(
            terms.terms_id, VendorTerms(terms=terms, verdict=verdict, received_at=datetime.now(UTC))
        )
        await self._audit(
            terms.vendor_id, "terms.received", terms.terms_id, terms, terms.retailer_id
        )
        self._publish("terms", terms.vendor_id, {"terms_id": terms.terms_id, "verdict": verdict})
        return Ack(ok=verdict != "PAYMENT_TOO_LATE", detail=verdict)

    async def vendor_terms(self, vendor_id: str) -> list[VendorTerms]:
        items = [t for t in await self.store.terms.query() if t.terms.vendor_id == vendor_id]
        return sorted(items, key=lambda t: t.received_at, reverse=True)

    async def answer_terms(self, vendor_id: str, terms_id: str, accept: bool) -> VendorTerms:
        record = await self.store.terms.get(terms_id)
        if record is None or record.terms.vendor_id != vendor_id:
            raise Problem(404, "unknown_terms", terms_id)
        if record.status != "proposed":
            raise Problem(409, "already_answered", record.status)
        verdict = await self._terms_verdict(record.terms)  # rules may have changed since
        if accept and verdict == "PAYMENT_TOO_LATE":
            raise Problem(
                409, "outside_your_rules", "change your rules first to accept these terms"
            )
        now = datetime.now(UTC)
        updated = record.model_copy(
            update={
                "status": "accepted" if accept else "declined",
                "verdict": verdict,
                "answered_at": now,
            }
        )
        await self.store.terms.put(terms_id, updated)
        await self.gateway(vendor_id).relay(
            agent_id_for(record.terms.retailer_id),
            "terms.response",
            TermsResponse(
                terms_id=terms_id,
                retailer_id=record.terms.retailer_id,
                vendor_id=vendor_id,
                accepted=accept,
                reason=verdict,
                answered_at=now,
            ),
            on_behalf_of=vendor_id,
            response_model=Ack,
        )
        self._publish("terms", vendor_id, updated)
        return updated

    # ---- consent ----------------------------------------------------------------------------

    async def _capture_consent(
        self, vendor_id: str, subject: Any, req: ConsentRequest
    ) -> ConsentRecord:
        vendor = await self.vendor(vendor_id)
        if not is_affirmative(req.transcript, self.strings(vendor, req.language)):
            raise Problem(422, "not_a_yes", "consent needs a clear yes")
        consent_id = uuid.uuid4().hex
        audio_uri = None
        if req.audio_b64:
            audio_uri = await self.blob.put(
                f"consent/{vendor_id}/{consent_id}", base64.b64decode(req.audio_b64), req.audio_mime
            )
        consent = ConsentRecord(
            id=consent_id,
            vendor_id=vendor_id,
            subject_hash=hash_obj(subject),
            audio_uri=audio_uri,
            transcript=req.transcript,
            language=req.language,
            captured_at=datetime.now(UTC),
        )
        await self.store.consents.put(consent.id, consent)
        await self.gateway(vendor_id).register_consent(consent)
        return consent

    # ---- rules ------------------------------------------------------------------------------

    async def draft_rules(self, vendor_id: str, draft: RulesDraft) -> tuple[StandingMandate, str]:
        vendor = await self.vendor(vendor_id)
        country = self.country(vendor)
        current = await self.mandate(vendor_id)
        mandate = StandingMandate(
            vendor_id=vendor_id,
            version=(current.version + 1) if current else 1,
            currency=country.currency,
            timezone=country.timezone,
            skus=[SkuRule.model_validate(s.model_dump()) for s in draft.skus],
            pickup_windows=draft.pickup_windows,
            payment_days_max=draft.payment_days_max,
            approved_buyers=draft.approved_buyers,
            auto_accept=AutoAccept(max_value=draft.auto_accept_max_value),
        )
        await self.store.rule_drafts.put(vendor_id, mandate)
        text = rules_readback(mandate, self.strings(vendor))
        self._publish(
            "rules",
            vendor_id,
            {
                "draft": mandate.model_dump(mode="json"),
                "subject_hash": hash_obj(mandate.unsigned()),
                "readback": text,
            },
        )
        return mandate, text

    async def consent_to_rules(self, vendor_id: str, req: ConsentRequest) -> StandingMandate:
        draft = await self.store.rule_drafts.get(vendor_id)
        if draft is None or draft.signature is not None:
            raise Problem(409, "nothing_to_confirm", "no rules draft")
        if hash_obj(draft.unsigned()) != req.subject_hash:
            raise Problem(409, "terms_changed", "read the latest rules back before consenting")
        consent = await self._capture_consent(vendor_id, draft.unsigned(), req)
        signed = await self.agents.call(
            "guardian.sign_mandate", SignMandateIn(mandate=draft, consent=consent), StandingMandate
        )
        await self.gateway(vendor_id).post_standing_mandate(signed)  # filed before in force
        await self.agents.call("guardian.install_mandate", signed, MandateOut)
        await self.store.rule_drafts.put(vendor_id, signed)  # marks the draft as used
        self._publish("rules", vendor_id, {"mandate": signed.model_dump(mode="json")})
        return signed

    # ---- listings ---------------------------------------------------------------------------

    async def _retailer(self, vendor: Vendor, retailer_id: str | None) -> str:
        retailer = retailer_id or (vendor.retailers[0] if vendor.retailers else None)
        if retailer is None:
            raise Problem(409, "no_retailer", "vendor is not working with any retailer yet")
        return retailer

    async def draft_listing(
        self, vendor_id: str, sub: ListingSubmission, retailer_id: str | None = None
    ) -> tuple[ListingStepOut, str, ListingApprovalOut | None]:
        vendor = await self.vendor(vendor_id)
        retailer = await self._retailer(vendor, retailer_id)
        if sub.photo_b64:
            screened = await self.gateway(vendor_id).screen(
                ScreenRequest(
                    filename="listing-photo",
                    content_type=sub.photo_mime,
                    data_b64=sub.photo_b64,
                    purpose="listing_photo",
                    subject=vendor_id,
                )
            )
            if not screened.allowed:
                raise Problem(422, "blocked_by_screening", ",".join(screened.findings))
        out = await self.gateway(vendor_id).relay(
            agent_id_for(retailer),
            "listing.submit",
            sub,
            on_behalf_of=vendor_id,
            response_model=ListingStepOut,
        )
        text = ""
        approval = None
        if out.requests:
            await self.hitl.ask_listing(vendor, retailer, sub, out.requests)
        if out.listing is not None:
            await self.store.listings.put(out.listing.id, out.listing)
            text = listing_readback(out.listing, self.strings(vendor))
            self._publish(
                "listing",
                vendor_id,
                {"listing": out.listing.model_dump(mode="json"), "readback": text},
            )
            if not out.requests:  # complete: her Guardian approves, or she is asked
                approval = await self._approve_listing(vendor, retailer, sub, out.listing)
        return out, text, approval

    async def _approve_listing(
        self, vendor: Vendor, retailer: str, sub: ListingSubmission, listing: Listing
    ) -> ListingApprovalOut:
        """Her Guardian approves an improved listing within her listing rules; otherwise the
        Liaison asks her in the app (photo choice and read-back). Never from Slack or email."""
        approval = await self.agents.call(
            "guardian.review_listing",
            ListingApprovalIn(
                listing=listing,
                transcript=sub.transcript,
                answers=sub.answers,
                rules=vendor.listing_rules,
            ),
            ListingApprovalOut,
        )
        if approval.auto_approve:
            log.info(
                "listing approved within her rules", listing_id=listing.id, image=approval.image
            )
            await self.confirm_listing(
                vendor.id, listing.id, retailer, image=approval.image, by="guardian"
            )
        else:
            log.info(
                "listing needs her look", listing_id=listing.id, reasons=",".join(approval.reasons)
            )
            await self.hitl.ask_listing_review(vendor, retailer, listing, approval.reasons)
        return approval

    async def confirm_listing(
        self,
        vendor_id: str,
        listing_id: str,
        retailer_id: str | None = None,
        *,
        image: str = "original",
        by: Literal["vendor", "guardian"] = "vendor",
    ) -> Ack:
        """Send the listing to the retailer with the photo she (or her Guardian) chose."""
        vendor = await self.vendor(vendor_id)
        listing = await self.store.listings.get(listing_id)
        if listing is None or listing.vendor_id != vendor_id:
            raise Problem(404, "unknown_listing", listing_id)
        chosen = next((c for c in listing.images if c.id == image), None)
        if image != "original" and (chosen is None or not chosen.uri):
            raise Problem(422, "unknown_image", image)
        generated = chosen is not None and chosen.kind == "generated"
        # any version she picked (auto-enhanced or AI) replaces her original photo
        new_photo = chosen is not None and chosen.kind != "original" and chosen.uri
        photos = [chosen.uri] if new_photo and chosen and chosen.uri else listing.photos
        retailer = await self._retailer(vendor, retailer_id)
        sent = listing.model_copy(
            update={
                "photos": photos,
                "ai_enhanced": generated,
                # the retailer keeps the full images; previews need not travel back
                "images": [c.model_copy(update={"preview_b64": None}) for c in listing.images],
            }
        )
        ack = await self.gateway(vendor_id).relay(
            agent_id_for(retailer),
            "listing.confirm",
            sent,
            on_behalf_of=vendor_id,
            response_model=Ack,
        )
        updated = listing.model_copy(
            update={
                "status": "published" if ack.ok else "needs_review",
                "photos": photos,
                "ai_enhanced": generated,
            }
        )
        await self.store.listings.put(listing_id, updated)
        await self.hitl.close(listing_id, "answered", f"confirmed by {by}")
        await self._audit(
            vendor_id,
            "listing.approved_by_guardian" if by == "guardian" else "listing.confirmed",
            listing_id,
            {"image": image, "ai_enhanced": generated},
            retailer,
        )
        self._publish("listing", vendor_id, {"listing": updated.model_dump(mode="json")})
        return ack

    async def withdraw_listing(self, vendor_id: str, listing_id: str) -> Listing:
        """She does not want this listing sent (answered "no" to the review)."""
        listing = await self.store.listings.get(listing_id)
        if listing is None or listing.vendor_id != vendor_id:
            raise Problem(404, "unknown_listing", listing_id)
        withdrawn = listing.model_copy(update={"status": "rejected"})
        await self.store.listings.put(listing_id, withdrawn)
        await self.hitl.close(listing_id, "answered", "withdrawn by her")
        await self._audit(vendor_id, "listing.withdrawn", listing_id, {"by": "vendor"})
        self._publish("listing", vendor_id, {"listing": withdrawn.model_dump(mode="json")})
        return withdrawn

    # ---- onboarding -------------------------------------------------------------------------

    async def submit_document(
        self,
        vendor_id: str,
        doc_type: str,
        number: str | None,
        data: bytes | None,
        content_type: str,
        filename: str,
    ) -> VendorDoc:
        vendor = await self.vendor(vendor_id)
        uri = ""
        status: str = "submitted"
        failure = None
        if data is not None:
            screened = await self.gateway(vendor_id).screen(
                ScreenRequest(
                    filename=filename,
                    content_type=content_type,
                    data_b64=base64.b64encode(data).decode(),
                    purpose="vendor_document",
                    subject=vendor_id,
                )
            )
            if not screened.allowed:
                status, failure = "failed", "BLOCKED_BY_SCREENING"
            else:
                uri = await self.blob.put(f"docs/{vendor_id}/{doc_type}", data, content_type)
        doc = VendorDoc.model_validate(
            {
                "doc_type": doc_type,
                "number": number,
                "uri": uri,
                "status": status,
                "failure_reason": failure,
            }
        )
        docs = [d for d in vendor.docs if d.doc_type != doc_type] + [doc]
        await self.store.vendors.put(vendor_id, vendor.model_copy(update={"docs": docs}))
        self._publish("document", vendor_id, doc)
        return doc

    async def apply(self, vendor_id: str, retailer_id: str | None = None) -> VendorCheckOut:
        vendor = await self.vendor(vendor_id)
        retailer = await self._retailer(vendor, retailer_id)
        if retailer not in vendor.retailers:
            vendor = vendor.model_copy(update={"retailers": [*vendor.retailers, retailer]})
            await self.store.vendors.put(vendor_id, vendor)
        application = VendorApplication(
            vendor=vendor,
            legal_name=vendor.legal_name or vendor.display_name,
            # a number that failed a registry check is checked again, so she sees why (not
            # "missing"); a document the gateway blocked in screening is never sent on
            doc_numbers={
                d.doc_type: d.number
                for d in vendor.docs
                if d.number and d.failure_reason != "BLOCKED_BY_SCREENING"
            },
        )
        result = await self.gateway(vendor_id).relay(
            agent_id_for(retailer),
            "vendor.onboard",
            application,
            on_behalf_of=vendor_id,
            response_model=VendorCheckOut,
        )
        by_type = {c.doc_type: c for c in result.checks}
        docs = []
        for d in vendor.docs:
            check = by_type.get(d.doc_type)
            if check is not None:
                d = d.model_copy(
                    update={
                        "status": "verified" if check.status == "pass" else "failed",
                        "failure_reason": None if check.status == "pass" else check.code,
                    }
                )
            docs.append(d)
        if result.requests:
            await self.hitl.ask_docs(vendor, retailer, result.requests)
        status = VendorStatus.PENDING_APPROVAL if result.passed else VendorStatus.VERIFYING
        await self.store.vendors.put(
            vendor_id, vendor.model_copy(update={"docs": docs, "status": status})
        )
        self._publish("status", vendor_id, result)
        return result

    async def on_listing_status(self, env: A2AEnvelope) -> Ack:
        """The retailer decided on her listing (published or rejected after a review)."""
        update = ListingStatusUpdate.model_validate(env.body)
        listing = await self.store.listings.get(update.listing_id)
        if listing is None or listing.vendor_id != update.vendor_id:
            raise Problem(404, "unknown_listing", update.listing_id)
        updated = listing.model_copy(
            update={"status": update.status, "ai_enhanced": update.ai_enhanced}
        )
        await self.store.listings.put(listing.id, updated)
        log.info(
            f"retailer {update.status} her listing",
            listing_id=listing.id,
            reasons=",".join(update.reasons) or None,
        )
        self._publish("listing", listing.vendor_id, {"listing": updated.model_dump(mode="json")})
        return Ack()

    async def on_vendor_status(self, env: A2AEnvelope) -> Ack:
        update = VendorStatusUpdate.model_validate(env.body)
        vendor = await self.vendor(update.vendor_id)
        await self.store.vendors.put(vendor.id, vendor.model_copy(update={"status": update.status}))
        self._publish("status", vendor.id, update)
        return Ack()

    # ---- views shared by the REST API and the Concierge's port --------------------------------

    async def deal_view_for(self, vendor_id: str, order_id: str, language: str | None) -> DealView:
        return await self.deal_view(await self.deal(order_id, vendor_id), language)

    async def rules_view(self, vendor_id: str, language: str | None = None) -> RulesView:
        vendor = await self.vendor(vendor_id)
        mandate = await self.mandate(vendor_id)
        draft = await self.store.rule_drafts.get(vendor_id)
        if draft is not None and draft.signature is not None:
            draft = None
        shown = draft or mandate
        s = self.strings(vendor, language)
        return RulesView(
            mandate=mandate,
            draft=draft,
            readback=rules_readback(shown, s) if shown else "",
            subject_hash=hash_obj(draft.unsigned()) if draft else None,
        )

    async def listing_draft_view(self, vendor_id: str, sub: ListingSubmission) -> ListingDraftView:
        result, text, approval = await self.draft_listing(vendor_id, sub)
        return ListingDraftView(result=result, readback=text, approval=approval)

    async def update_profile(self, vendor_id: str, update: ProfileUpdate) -> Vendor:
        vendor = await self.vendor(vendor_id)
        changes = update.model_dump(exclude_none=True)
        if "language" in changes:
            self.strings(vendor, changes["language"])  # 422 unless her country offers it
        updated = vendor.model_copy(update=changes)
        await self.store.vendors.put(vendor_id, updated)
        await self._audit(vendor_id, "profile.update", vendor_id, changes)
        return updated

    async def audit(self, vendor_id: str) -> list[AuditRecord]:
        async with make_client(self.settings.trust_gateway_url) as client:
            response = await client.get("/audit", params={"party": vendor_id})
            response.raise_for_status()
            return [AuditRecord.model_validate(r) for r in response.json()]

    async def today(self, vendor_id: str, language: str | None = None) -> list[str]:
        """What agents did for the vendor today, in her words (deduplicated)."""
        vendor = await self.vendor(vendor_id)
        known = self.strings(vendor, language).get("activity")
        today = datetime.now(UTC).date()
        seen: list[str] = []
        for record in await self.audit(vendor_id):
            if record.ts.date() == today and record.action in known:
                text = str(known[record.action])
                if text not in seen:
                    seen.append(text)
        return seen

    # ---- supplier self-registration and the retailer network ----------------------------------

    async def register(self, req: VendorRegistration) -> Vendor:
        """A new supplier signs up: a vendor record plus her own agent identity at the gateway."""
        country = self.loader.load("country_packs", req.country)
        assert isinstance(country, CountryPack)
        self.loader.load("business_profiles", req.business_profile)  # must exist
        if req.language not in country.languages:
            raise Problem(422, "unsupported_language", req.language)
        slug = re.sub(r"[^a-z0-9]+", "-", req.display_name.casefold()).strip("-")[:24] or "vendor"
        vendor_id = f"vendor:{slug}"
        if await self.store.vendors.get(vendor_id) is not None:
            vendor_id = f"vendor:{slug}-{uuid.uuid4().hex[:4]}"
        vendor = Vendor(
            id=vendor_id,
            display_name=req.display_name,
            legal_name=req.legal_name,
            business_profile=req.business_profile,
            country=req.country,
            language=req.language,
            retailers=req.retailers,
            email=req.email,
        )
        vendor = await self.register_vendor(vendor)
        await self._audit(vendor_id, "vendor.register", vendor_id, vendor)
        return vendor

    async def required_docs(self, vendor_id: str) -> RequiredDocs:
        vendor = await self.vendor(vendor_id)
        bp = self.loader.load("business_profiles", vendor.business_profile)
        assert isinstance(bp, BusinessProfile)
        country = self.country(vendor)
        specs = [*country.id_docs, *country.food_safety_regs]
        return RequiredDocs(
            roles={role: [s for s in specs if s.role == role] for role in bp.required_docs}
        )

    async def directory(self, party_type: str) -> list[DirectoryEntry]:
        async with make_client(self.settings.trust_gateway_url) as client:
            response = await client.get("/agents")
            response.raise_for_status()
        return [
            DirectoryEntry(
                party_id=a["on_behalf_of"],
                party_type=party_type,
                display_name=a.get("display_name") or a["on_behalf_of"],
                agent_id=a["agent_id"],
                status=a["status"],
            )
            for a in response.json()
            if a.get("kind", "party") == "party" and a["party_type"] == party_type
        ]

    async def options(self) -> VendorOptions:
        countries = []
        for path in sorted((self.loader.root / "country_packs").glob("*.json")):
            if "@" in path.stem:
                continue
            cp = self.loader.load("country_packs", path.stem)
            assert isinstance(cp, CountryPack)
            countries.append(
                CountryOption(
                    code=cp.code,
                    name=cp.name,
                    languages=cp.languages,
                    default_language=cp.default_language,
                    currency=cp.currency,
                )
            )
        profiles = []
        for path in sorted((self.loader.root / "business_profiles").glob("*.json")):
            if "@" in path.stem:
                continue
            bp = self.loader.load("business_profiles", path.stem)
            assert isinstance(bp, BusinessProfile)
            profiles.append(
                BusinessOption(
                    id=bp.id, name=bp.name, units=bp.units, required_docs=bp.required_docs
                )
            )
        return VendorOptions(
            countries=countries,
            business_profiles=profiles,
            retailers=await self.directory("retailer"),
        )

    async def invites(self, vendor_id: str) -> list[VendorInvite]:
        return sorted(
            await self.store.invites.query(vendor_id=vendor_id),
            key=lambda i: i.sent_at,
            reverse=True,
        )

    async def on_invite(self, env: A2AEnvelope) -> Ack:
        invite = VendorInvite.model_validate(env.body)
        await self.vendor(invite.vendor_id)
        await self.store.invites.put(invite.invite_id, invite)
        await self._audit(
            invite.vendor_id, "vendor.invited", invite.retailer_id, invite, invite.retailer_id
        )
        self._publish("status", invite.vendor_id, {"invite": invite.model_dump(mode="json")})
        return Ack(detail="invitation delivered")
