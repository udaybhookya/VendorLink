"""In-process VendorPort: lets the Concierge agent act on vendor state inside vendor-svc."""

from __future__ import annotations

from vendor_svc.operations import VendorOperations
from vl_core.schemas import (
    Ack,
    ConsentRequest,
    DealView,
    DocumentUpload,
    HumanTask,
    ListingDraftView,
    ListingSubmission,
    ProfileUpdate,
    RulesDraft,
    RulesView,
    StandingMandate,
    Vendor,
    VendorDoc,
)


class InProcessVendorPort:
    def __init__(self, services: VendorOperations) -> None:
        self.s = services

    async def vendor(self, vendor_id: str) -> Vendor:
        return await self.s.vendor(vendor_id)

    async def deals(self, vendor_id: str, language: str) -> list[DealView]:
        return [await self.s.deal_view(d, language) for d in await self.s.deals(vendor_id)]

    async def deal(self, vendor_id: str, order_id: str, language: str) -> DealView:
        return await self.s.deal_view_for(vendor_id, order_id, language)

    async def consent_deal(self, vendor_id: str, order_id: str, req: ConsentRequest) -> DealView:
        return await self.s.consent_to_deal(vendor_id, order_id, req)

    async def decline_deal(self, vendor_id: str, order_id: str) -> DealView:
        return await self.s.decline_deal(vendor_id, order_id)

    async def rules(self, vendor_id: str, language: str) -> RulesView:
        return await self.s.rules_view(vendor_id, language)

    async def draft_rules(self, vendor_id: str, draft: RulesDraft) -> RulesView:
        await self.s.draft_rules(vendor_id, draft)
        return await self.s.rules_view(vendor_id)

    async def consent_rules(self, vendor_id: str, req: ConsentRequest) -> StandingMandate:
        return await self.s.consent_to_rules(vendor_id, req)

    async def draft_listing(self, vendor_id: str, sub: ListingSubmission) -> ListingDraftView:
        return await self.s.listing_draft_view(vendor_id, sub)

    async def confirm_listing(self, vendor_id: str, listing_id: str) -> Ack:
        return await self.s.confirm_listing(vendor_id, listing_id)

    async def submit_document(self, vendor_id: str, upload: DocumentUpload) -> VendorDoc:
        import base64

        data = base64.b64decode(upload.data_b64) if upload.data_b64 else None
        return await self.s.submit_document(
            vendor_id, upload.doc_type, upload.number, data, upload.content_type, upload.filename
        )

    async def update_profile(self, vendor_id: str, update: ProfileUpdate) -> Vendor:
        return await self.s.update_profile(vendor_id, update)

    async def today(self, vendor_id: str, language: str) -> list[str]:
        return await self.s.today(vendor_id, language)

    async def questions(self, vendor_id: str) -> list[HumanTask]:
        return await self.s.hitl.questions(vendor_id)

    async def answer_question(self, vendor_id: str, task_id: str, text: str) -> HumanTask:
        return await self.s.hitl.answer(vendor_id, task_id, text)
