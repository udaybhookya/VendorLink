"""Vendor side of human in the loop: what we ask Maya, and what happens when she answers.

Questions reach her in the app (and the Concierge reads them out); if she has an email on file,
reminders nudge her back to the app. Consent itself stays in-app/voice with the read-back.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from vl_core.a2a import agent_id_for
from vl_core.errors import Problem
from vl_core.obs import get_logger, trace_id_var
from vl_core.readback import party_name
from vl_core.schemas import (
    A2AEnvelope,
    Ack,
    AnswerIn,
    AskRequest,
    Choice,
    Contact,
    ContactBook,
    Deal,
    HumanTask,
    InfoRequest,
    InfoRequestMessage,
    InfoResponseMessage,
    Listing,
    ListingSubmission,
    OutboxMessage,
    TaskList,
    TaskQuery,
    TaskRef,
    Vendor,
)
from vl_core.store import get_repo

if TYPE_CHECKING:
    from vendor_svc.operations import VendorOperations

log = get_logger("vendor.hitl")


class PendingListing(BaseModel):
    id: str
    vendor_id: str
    retailer_id: str
    submission: ListingSubmission
    fields: list[str]
    answers: dict[str, str] = Field(default_factory=dict)


class VendorHumanLoop:
    def __init__(self, services: VendorOperations) -> None:
        self.s = services
        self.pending = get_repo(PendingListing, "vendor.pending_listings", services.settings)
        self.info = get_repo(InfoRequestMessage, "vendor.info_requests", services.settings)

    # ---- resources for the Liaison --------------------------------------------------------------

    async def contacts(self, party: str) -> ContactBook:
        vendor = await self.s.vendor(party)
        return ContactBook(
            party=party,
            contacts=[
                Contact(role="vendor", name=vendor.display_name, inapp=True, email=vendor.email)
            ],
        )

    def publish(self, to: str, entry: OutboxMessage) -> None:
        self.s._publish("question", to, entry)

    # ---- asking --------------------------------------------------------------------------------

    async def ask(
        self, vendor: Vendor, request: InfoRequest, *, subject_ref: str, resume: str, task_id: str
    ) -> HumanTask:
        return await self.s.agents.call(
            "human.ask",
            AskRequest(
                party=vendor.id,
                request=request,
                language=vendor.language,
                subject_ref=subject_ref,
                resume=resume,
                task_id=task_id,
                trace_id=trace_id_var.get() or "",
            ),
            HumanTask,
        )

    async def ask_consent(self, deal: Deal) -> None:
        vendor = await self.s.vendor(deal.vendor_id)
        await self.ask(
            vendor,
            InfoRequest(
                field="consent",
                ask="vendor",
                kind="consent",
                answer_type="text",
                question_key="hitl.q.consent",
                params={"retailer": party_name(deal.retailer_id), "order": deal.order_id},
            ),
            subject_ref=deal.order_id,
            resume=f"deal:{deal.order_id}",
            task_id=f"consent-{deal.order_id}-v{deal.latest.version}",
        )

    async def close(self, subject_ref: str, outcome: str, reason: str) -> None:
        tasks = await self.s.agents.call(
            "human.list", TaskQuery(subject_ref=subject_ref, status="open"), TaskList
        )
        for task in tasks.tasks:
            await self.s.agents.call(
                "human.cancel", TaskRef(task_id=task.id, reason=reason, outcome=outcome), HumanTask
            )

    async def ask_listing(
        self, vendor: Vendor, retailer: str, sub: ListingSubmission, requests: list[InfoRequest]
    ) -> None:
        pending = PendingListing(
            id=f"pl-{uuid.uuid4().hex[:8]}",
            vendor_id=vendor.id,
            retailer_id=retailer,
            submission=sub,
            fields=[r.field for r in requests],
            answers=dict(sub.answers),
        )
        await self.pending.put(pending.id, pending)
        for r in requests:
            await self.ask(
                vendor,
                r,
                subject_ref=pending.id,
                resume=f"listing:{pending.id}:{r.field}",
                task_id=f"{pending.id}-{r.field}",
            )

    async def ask_listing_review(
        self, vendor: Vendor, retailer: str, listing: Listing, reasons: list[str]
    ) -> None:
        """Her agent could not approve the improved listing alone: ask her, in the app, which
        photo to use (her own or an enhanced version) after reading the listing back."""
        strings = self.s.strings(vendor)
        rendered = [c for c in listing.images if c.kind == "generated" and c.uri]
        enhanced = [c for c in listing.images if c.kind == "enhanced" and c.uri]
        choices = [
            Choice(
                id="original",
                label=strings.t("hitl.choice.my_photo"),
                words=strings.words("hitl.choice_words.my_photo"),
            ),
            *(
                Choice(
                    id=c.id,
                    label=strings.t("hitl.choice.enhanced"),
                    words=strings.words("hitl.choice_words.enhanced"),
                )
                for c in enhanced
            ),
            *(
                # a styled version is named by its look ("Warm wood"), so she can say it back
                Choice(
                    id=c.id,
                    label=c.look_name.get(strings.lang)
                    or c.look_name.get("en")
                    or strings.t("hitl.choice.photo", n=n),
                    words=[w for w in (c.look_name.get(strings.lang), c.look_name.get("en")) if w],
                )
                for n, c in enumerate(rendered, start=1)
            ),
            Choice(
                id="reject",
                label=strings.t("hitl.choice.dont_list"),
                words=strings.words("negatives"),
            ),
        ]
        await self.ask(
            vendor,
            InfoRequest(
                field="listing_review",
                ask="vendor",
                kind="review",
                answer_type="choice",
                question_key="hitl.q.listing_review_vendor",
                params={
                    "sku": listing.title or listing.sku_name,
                    "retailer": party_name(retailer),
                    "reasons": "; ".join(strings.t(f"listing_reason.{r}") for r in reasons),
                },
                choices=choices,
            ),
            subject_ref=listing.id,
            resume=f"listing_review:{listing.id}",
            task_id=f"review-{listing.id}",
        )

    async def ask_docs(self, vendor: Vendor, retailer: str, requests: list[InfoRequest]) -> None:
        for r in requests:
            await self.ask(
                vendor,
                r,
                subject_ref=f"onboarding:{retailer}",
                resume=f"doc:{retailer}|{r.field}",
                task_id=f"{vendor.id}-{r.field}",
            )

    async def on_info_request(self, env: A2AEnvelope) -> Ack:
        msg = InfoRequestMessage.model_validate(env.body)
        vendor = await self.s.vendor(msg.vendor_id)
        await self.info.put(msg.request_id, msg)
        request = msg.request.model_copy(
            update={
                "ask": "vendor",
                "params": {**msg.request.params, "retailer": party_name(msg.retailer_id)},
            }
        )
        await self.ask(
            vendor,
            request,
            subject_ref=msg.subject_ref,
            resume=f"info:{msg.request_id}",
            task_id=f"info-{msg.request_id}",
        )
        return Ack(detail="asked the vendor")

    # ---- answers -------------------------------------------------------------------------------

    async def questions(self, vendor_id: str) -> list[HumanTask]:
        """Her open questions, worded in the language she uses now (she may have switched since
        they were asked; the question key and its values are stored, so we word them again)."""
        out = await self.s.agents.call(
            "human.list", TaskQuery(party=vendor_id, status="open"), TaskList
        )
        vendor = await self.s.vendor(vendor_id)
        strings = self.s.strings(vendor)
        worded = []
        for task in out.tasks:
            try:
                text = strings.t(task.request.question_key, **task.request.params)
            except KeyError:  # a key this language lacks: keep the wording it was asked in
                worded.append(task)
                continue
            worded.append(task.model_copy(update={"question": text, "language": vendor.language}))
        return worded

    async def answer(self, vendor_id: str, task_id: str, text: str) -> HumanTask:
        task = next((t for t in await self.questions(vendor_id) if t.id == task_id), None)
        if task is None:
            raise Problem(404, "unknown_question", task_id)
        if task.kind == "consent":
            raise Problem(
                409, "answer_in_deals", "consent is given on the deal, after the read-back"
            )
        return await self.s.agents.call(
            "human.answer",
            AnswerIn(task_id=task_id, text=text, by=vendor_id, via="inapp"),
            HumanTask,
        )

    async def resolved(self, task: HumanTask) -> None:
        kind, _, rest = task.resume.partition(":")
        if kind == "deal":
            if task.status == "expired" and task.answer_value == "reject":
                deal = await self.s.store.deals.get(rest)
                if deal is not None and deal.status == "awaiting_consent":
                    await self.s.decline_deal(deal.vendor_id, deal.order_id)
        elif kind == "listing" and task.status == "answered":
            pending_id, field = rest.split(":", 1)
            pending = await self.pending.get(pending_id)
            if pending is None:
                return
            answers = {**pending.answers, field: str(task.answer_value)}
            await self.pending.put(pending.id, pending.model_copy(update={"answers": answers}))
            if all(f in answers for f in pending.fields):
                sub = pending.submission.model_copy(update={"answers": answers})
                await self.s.draft_listing(pending.vendor_id, sub, pending.retailer_id)
        elif kind == "listing_review" and task.status == "answered":
            if task.answered_via != "inapp":  # her listing choice counts only from the app
                log.warning("listing choice ignored: not from the app", task=task.id)
                return
            if task.answer_value == "reject":
                await self.s.withdraw_listing(task.party, rest)
            else:
                await self.s.confirm_listing(task.party, rest, image=str(task.answer_value))
        elif kind == "doc" and task.status == "answered":
            retailer, field = rest.split("|", 1)  # party ids contain ':'
            await self.s.submit_document(
                task.party,
                field.removeprefix("doc:"),
                str(task.answer_value),
                None,
                "text/plain",
                "answer",
            )
            if not [
                t
                for t in await self.questions(task.party)
                if t.subject_ref == f"onboarding:{retailer}"
            ]:
                await self.s.apply(task.party, retailer)
        elif kind == "info":
            msg = await self.info.get(rest)
            if msg is None:
                return
            status = task.status if task.status in ("answered", "expired") else "cancelled"
            response = InfoResponseMessage(
                request_id=msg.request_id,
                retailer_id=msg.retailer_id,
                vendor_id=msg.vendor_id,
                answer=task.answer or "",
                answer_value=task.answer_value,
                status=status,
            )
            await self.s.gateway(msg.vendor_id).relay(
                agent_id_for(msg.retailer_id),
                "info.response",
                response,
                on_behalf_of=msg.vendor_id,
                response_model=Ack,
            )
