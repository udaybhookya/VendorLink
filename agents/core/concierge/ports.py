"""What the Concierge may do to vendor state. In vendor-svc this is an in-process adapter; a
standalone Concierge uses `HttpVendorPort` against the vendor REST API."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, TypeAdapter

from vl_core.a2a import make_client
from vl_core.errors import Problem
from vl_core.schemas import (
    Ack,
    ConsentRequest,
    DealView,
    DocumentUpload,
    HumanTask,
    ListingDraftView,
    ListingSubmission,
    ProfileUpdate,
    QuestionAnswer,
    RulesDraft,
    RulesView,
    StandingMandate,
    Vendor,
    VendorDoc,
)


class VendorPort(Protocol):
    async def vendor(self, vendor_id: str) -> Vendor: ...
    async def deals(self, vendor_id: str, language: str) -> list[DealView]: ...
    async def deal(self, vendor_id: str, order_id: str, language: str) -> DealView: ...
    async def consent_deal(
        self, vendor_id: str, order_id: str, req: ConsentRequest
    ) -> DealView: ...
    async def decline_deal(self, vendor_id: str, order_id: str) -> DealView: ...
    async def rules(self, vendor_id: str, language: str) -> RulesView: ...
    async def draft_rules(self, vendor_id: str, draft: RulesDraft) -> RulesView: ...
    async def consent_rules(self, vendor_id: str, req: ConsentRequest) -> StandingMandate: ...
    async def draft_listing(self, vendor_id: str, sub: ListingSubmission) -> ListingDraftView: ...
    async def confirm_listing(self, vendor_id: str, listing_id: str) -> Ack: ...
    async def submit_document(self, vendor_id: str, upload: DocumentUpload) -> VendorDoc: ...
    async def update_profile(self, vendor_id: str, update: ProfileUpdate) -> Vendor: ...
    async def today(self, vendor_id: str, language: str) -> list[str]: ...
    async def questions(self, vendor_id: str) -> list[HumanTask]: ...
    async def answer_question(self, vendor_id: str, task_id: str, text: str) -> HumanTask: ...


class HttpVendorPort:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def _call[T](
        self,
        method: str,
        path: str,
        out: type[T],
        body: BaseModel | None = None,
        params: dict[str, str] | None = None,
    ) -> T:
        async with make_client(self.base_url) as client:
            response = await client.request(
                method,
                path,
                params=params,
                json=body.model_dump(mode="json") if body is not None else None,
            )
        if response.status_code >= 400:
            detail = response.json().get("detail", {}) if response.content else {}
            if not isinstance(detail, dict):
                detail = {"error": "error", "detail": str(detail)}
            raise Problem(
                response.status_code,
                str(detail.get("error", "error")),
                str(detail.get("detail", "")),
            )
        return TypeAdapter(out).validate_python(response.json())

    async def vendor(self, vendor_id: str) -> Vendor:
        return await self._call("GET", f"/vendors/{vendor_id}/me", Vendor)

    async def deals(self, vendor_id: str, language: str) -> list[DealView]:
        return await self._call(
            "GET",
            f"/vendors/{vendor_id}/deals",
            list[DealView],
            params={"language": language},
        )

    async def deal(self, vendor_id: str, order_id: str, language: str) -> DealView:
        return await self._call(
            "GET",
            f"/vendors/{vendor_id}/deals/{order_id}",
            DealView,
            params={"language": language},
        )

    async def consent_deal(self, vendor_id: str, order_id: str, req: ConsentRequest) -> DealView:
        return await self._call(
            "POST",
            f"/vendors/{vendor_id}/deals/{order_id}/consent",
            DealView,
            req,
        )

    async def decline_deal(self, vendor_id: str, order_id: str) -> DealView:
        return await self._call(
            "POST",
            f"/vendors/{vendor_id}/deals/{order_id}/decline",
            DealView,
        )

    async def rules(self, vendor_id: str, language: str) -> RulesView:
        return await self._call(
            "GET",
            f"/vendors/{vendor_id}/rules",
            RulesView,
            params={"language": language},
        )

    async def draft_rules(self, vendor_id: str, draft: RulesDraft) -> RulesView:
        return await self._call("POST", f"/vendors/{vendor_id}/rules/draft", RulesView, draft)

    async def consent_rules(self, vendor_id: str, req: ConsentRequest) -> StandingMandate:
        return await self._call(
            "POST",
            f"/vendors/{vendor_id}/rules/consent",
            StandingMandate,
            req,
        )

    async def draft_listing(self, vendor_id: str, sub: ListingSubmission) -> ListingDraftView:
        return await self._call(
            "POST",
            f"/vendors/{vendor_id}/listings/draft",
            ListingDraftView,
            sub,
        )

    async def confirm_listing(self, vendor_id: str, listing_id: str) -> Ack:
        return await self._call(
            "POST",
            f"/vendors/{vendor_id}/listings/{listing_id}/confirm",
            Ack,
        )

    async def submit_document(self, vendor_id: str, upload: DocumentUpload) -> VendorDoc:
        return await self._call("POST", f"/vendors/{vendor_id}/documents", VendorDoc, upload)

    async def update_profile(self, vendor_id: str, update: ProfileUpdate) -> Vendor:
        return await self._call("PATCH", f"/vendors/{vendor_id}/profile", Vendor, update)

    async def today(self, vendor_id: str, language: str) -> list[str]:
        return await self._call(
            "GET",
            f"/vendors/{vendor_id}/today",
            list[str],
            params={"language": language},
        )

    async def questions(self, vendor_id: str) -> list[HumanTask]:
        return await self._call("GET", f"/vendors/{vendor_id}/questions", list[HumanTask])

    async def answer_question(self, vendor_id: str, task_id: str, text: str) -> HumanTask:
        return await self._call(
            "POST",
            f"/vendors/{vendor_id}/questions/{task_id}/answer",
            HumanTask,
            QuestionAnswer(text=text),
        )
