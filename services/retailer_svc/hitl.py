"""Retailer side of human in the loop: staff are asked on Slack / Gmail (per config), vendors are
asked through the gateway (info.request), and answers resume the queue or a paused job."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from vl_core.a2a import agent_id_for
from vl_core.i18n import Strings
from vl_core.obs import trace_id_var
from vl_core.readback import party_name
from vl_core.schemas import (
    A2AEnvelope,
    Ack,
    AnswerIn,
    AskRequest,
    Choice,
    ContactBook,
    HumanTask,
    InfoRequest,
    InfoRequestMessage,
    InfoResponseMessage,
    OutboxMessage,
    TaskList,
    TaskQuery,
    TaskRef,
    TermsResponse,
)
from vl_core.store import get_repo

if TYPE_CHECKING:
    from retailer_svc.operations import RetailerOperations

# queue kind → (task kind, role asked, question)
QUEUE_ASKS: dict[str, tuple[str, str, str]] = {
    "place_order": ("approval", "buyer", "hitl.q.order_over_limit"),
    "handle_counter": ("approval", "buyer", "hitl.q.counter_approval"),
    "vendor": ("approval", "category_manager", "hitl.q.vendor_approval"),
    "listing": ("review", "category_manager", "hitl.q.listing_review"),
}


def approval_choices(strings: Strings) -> list[Choice]:
    """Approve / reject, labelled and recognised in the retailer staff's language."""
    return [
        Choice(
            id=c,
            label=strings.get(f"hitl.choice.{c}"),
            words=strings.words(f"hitl.choice_words.{c}"),
        )
        for c in ("approve", "reject")
    ]


class RetailerHumanLoop:
    def __init__(self, ops: RetailerOperations) -> None:
        self.ops = ops
        self.responses = get_repo(InfoResponseMessage, "retailer.info_responses", ops.settings)
        self.terms = get_repo(TermsResponse, "retailer.terms_responses", ops.settings)

    async def language(self, retailer_id: str) -> str:
        return (await self.contacts(retailer_id)).language

    async def approve_choices(self, retailer_id: str) -> list[Choice]:
        language = await self.language(retailer_id)
        return approval_choices(Strings(language, self.ops.settings.vl_config_dir / "i18n"))

    # ---- resources for the Liaison --------------------------------------------------------------

    async def contacts(self, party: str) -> ContactBook:
        path = self.ops.settings.vl_config_dir / "contacts" / f"{party.replace(':', '_')}.json"
        if not path.exists():
            return ContactBook(party=party, contacts=[])
        return ContactBook.model_validate(json.loads(path.read_text()))

    def publish(self, to: str, entry: OutboxMessage) -> None:
        self.ops._publish("task", {"to": to, "message": entry.model_dump(mode="json")})

    # ---- asking staff --------------------------------------------------------------------------

    async def ask(
        self, retailer_id: str, request: InfoRequest, *, subject_ref: str, resume: str, task_id: str
    ) -> HumanTask:
        return await self.ops.agents.call(
            "human.ask",
            AskRequest(
                party=retailer_id,
                request=request,
                language=await self.language(retailer_id),
                subject_ref=subject_ref,
                resume=resume,
                task_id=task_id,
                trace_id=trace_id_var.get() or "",
            ),
            HumanTask,
        )

    async def ask_approval(
        self, retailer_id: str, queue_kind: str, ref_id: str, item_id: str, params: dict[str, Any]
    ) -> HumanTask:
        kind, role, key = QUEUE_ASKS[queue_kind]
        request = InfoRequest(
            field="approval",
            ask=role,
            kind=kind,
            answer_type="choice",
            question_key=key,
            choices=await self.approve_choices(retailer_id),
            params={k: str(v) for k, v in params.items()},
        )
        return await self.ask(
            retailer_id,
            request,
            subject_ref=ref_id,
            resume=f"queue:{item_id}",
            task_id=f"q-{item_id}",
        )

    async def notify(
        self, retailer_id: str, role: str, key: str, params: dict[str, Any], ref: str
    ) -> None:
        request = InfoRequest(
            field="notice",
            ask=role,
            kind="notice",
            question_key=key,
            params={k: str(v) for k, v in params.items()},
        )
        await self.ask(
            retailer_id, request, subject_ref=ref, resume="notice", task_id=f"notice-{ref}-{key}"
        )

    async def close(self, task_id: str, reason: str) -> None:
        await self.ops.agents.call(
            "human.cancel", TaskRef(task_id=task_id, reason=reason, outcome="answered"), HumanTask
        )

    async def tasks(self, retailer_id: str, status: str | None = None) -> list[HumanTask]:
        out = await self.ops.agents.call(
            "human.list",
            TaskQuery(party=retailer_id, status=status),
            TaskList,
        )
        return out.tasks

    async def answer(self, retailer_id: str, task_id: str, text: str, by: str) -> HumanTask:
        return await self.ops.agents.call(
            "human.answer",
            AnswerIn(task_id=task_id, text=text, by=by or f"console:{retailer_id}", via="inapp"),
            HumanTask,
        )

    # ---- asking vendors (through the gateway) --------------------------------------------------

    async def info_request(
        self,
        retailer_id: str,
        vendor_id: str,
        request: InfoRequest,
        subject_ref: str,
        request_id: str,
    ) -> None:
        msg = InfoRequestMessage(
            request_id=request_id,
            retailer_id=retailer_id,
            vendor_id=vendor_id,
            request=request,
            subject_ref=subject_ref,
        )
        await self.ops.gateway(retailer_id).relay(
            agent_id_for(vendor_id),
            "info.request",
            msg,
            on_behalf_of=retailer_id,
            response_model=Ack,
        )

    async def on_terms_response(self, env: A2AEnvelope) -> Ack:
        msg = TermsResponse.model_validate(env.body)
        await self.terms.put(msg.terms_id, msg)
        await self.ops._audit(msg.retailer_id, "terms.response", msg.terms_id, msg)
        self.ops._publish("terms", msg)
        return Ack(detail="terms answer received")

    async def on_info_response(self, env: A2AEnvelope) -> Ack:
        msg = InfoResponseMessage.model_validate(env.body)
        await self.responses.put(msg.request_id, msg)
        await self.ops._audit(msg.retailer_id, "info.response", msg.request_id, msg)
        await self.notify(
            msg.retailer_id,
            "buyer",
            "hitl.q.info_answer",
            {
                "vendor": party_name(msg.vendor_id),
                "answer": msg.answer or msg.status,
                "subject": msg.request_id,
            },
            msg.request_id,
        )
        self.ops._publish("info_response", msg)
        return Ack(detail="answer received")

    # ---- answers resume work -------------------------------------------------------------------

    async def resolved(self, task: HumanTask) -> None:
        kind, _, ref = task.resume.partition(":")
        if kind == "queue":
            if task.status == "answered":
                await self.ops.resolve(
                    task.party, ref, task.answer_value == "approve", from_task=True
                )
            elif task.status == "expired" and task.answer_value == "reject":
                await self.ops.resolve(task.party, ref, False, from_task=True)
        elif kind == "job":
            await self.ops.jobs.resume(ref, task)
