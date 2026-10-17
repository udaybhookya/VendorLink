"""The Concierge's whole action surface. There is deliberately no signing tool here.

`capture_consent` hands off through the vendor port; only the Guardian signs, after re-checking
the mandate and the consent hash.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agents.core.concierge.ports import VendorPort
from agents.core.concierge.session import ConciergeSession, Pending
from vl_core.a2a import GatewayError
from vl_core.errors import Problem
from vl_core.i18n import Strings
from vl_core.llm import ToolSpec
from vl_core.obs import get_logger
from vl_core.readback import rules_readback
from vl_core.schemas import (
    ConsentRequest,
    DealView,
    DocumentUpload,
    ListingSubmission,
    ProfileUpdate,
    RulesDraft,
)

log = get_logger("agent.concierge")


class NoArgs(BaseModel):
    pass


class OrderArgs(BaseModel):
    order_id: str


class ConsentArgs(BaseModel):
    subject: Literal["deal", "rules"]
    subject_id: str = Field(description="order_id for a deal, vendor_id for rules")


class ListingArgs(BaseModel):
    spoken_text: str = Field(description="What the vendor said about the product")


class ListingIdArgs(BaseModel):
    listing_id: str


class DocumentArgs(BaseModel):
    doc_type: str = Field(description="Document id from the country pack, e.g. aadhaar")
    number: str


class QuestionArgs(BaseModel):
    task_id: str = Field(description="The question just read out (from list_questions)")


class ProfileArgs(BaseModel):
    field: Literal["display_name", "legal_name"]
    value: str


ToolFn = Callable[[BaseModel], Awaitable[dict[str, Any]]]


class ConciergeTools:
    def __init__(self, port: VendorPort, session: ConciergeSession, i18n_dir: Path) -> None:
        self.port = port
        self.session = session
        self.i18n_dir = i18n_dir
        self.events: list[dict[str, Any]] = []
        self._tools: dict[str, tuple[str, type[BaseModel], ToolFn]] = {
            "list_deals": ("List the vendor's deals with their colour.", NoArgs, self.list_deals),
            "explain_decision": (
                "Read back one deal and why it got its colour.",
                OrderArgs,
                self.explain_decision,
            ),
            "capture_consent": (
                "Record the vendor's spoken yes to what was just read back.",
                ConsentArgs,
                self.capture_consent,
            ),
            "decline": (
                "The vendor said no to the deal or rules just read back.",
                NoArgs,
                self.decline,
            ),
            "show_rules": ("Read back the vendor's current rules.", NoArgs, self.show_rules),
            "draft_rules": (
                "Turn the vendor's spoken rules into a draft and read it back.",
                RulesDraft,
                self.draft_rules,
            ),
            "draft_listing": (
                "Send the last photo and the vendor's words to build a listing.",
                ListingArgs,
                self.draft_listing,
            ),
            "confirm_listing": (
                "Confirm a listing the vendor agreed to after read-back.",
                ListingIdArgs,
                self.confirm_listing,
            ),
            "submit_document": (
                "Submit the last photo as an identity or licence document.",
                DocumentArgs,
                self.submit_document,
            ),
            "save_profile_field": ("Save the vendor's name.", ProfileArgs, self.save_profile_field),
            "list_questions": (
                "Read out the next question agents are waiting on her to answer.",
                NoArgs,
                self.list_questions,
            ),
            "answer_question": (
                "Record her answer to the question just read out. Uses her own words.",
                QuestionArgs,
                self.answer_question,
            ),
            "what_did_you_do_today": (
                "Say what agents did for the vendor today.",
                NoArgs,
                self.what_did_you_do_today,
            ),
        }

    @property
    def strings(self) -> Strings:
        return Strings(self.session.language, self.i18n_dir)

    @property
    def vendor_id(self) -> str:
        return self.session.vendor_id

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name, desc, model) for name, (desc, model, _) in self._tools.items()]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self._call(name, args)
        # ids only: argument values can be her words, names or document numbers
        ids = {k: v for k, v in args.items() if k.endswith("_id") or k == "subject"}
        if "error" in result:
            log.warning(f"tool {name} refused", error=result["error"], fields=ids)
        else:
            log.info(f"tool {name}", fields=ids)
        return result

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            return {"error": "unknown_tool"}
        _, model, fn = entry
        try:
            return await fn(model.model_validate(args))
        except ValidationError as exc:
            return {"error": "bad_arguments", "detail": str(exc.errors(include_input=False))}
        except (Problem, GatewayError) as exc:
            return {"error": exc.code, "detail": exc.detail}

    # ---- deals ------------------------------------------------------------------------------

    async def list_deals(self, _: BaseModel) -> dict[str, Any]:
        views = await self.port.deals(self.vendor_id, self.session.language)
        return {
            "deals": [
                {
                    "order_id": v.deal.order_id,
                    "what": deal_summary(v),
                    "store_id": v.deal.store_id,
                    "status": v.deal.status,
                    "colour": v.deal.decisions[-1].status.value if v.deal.decisions else None,
                    "status_text": self.strings.t(f"deal_status.{v.deal.status}"),
                }
                for v in views
            ]
        }

    async def explain_decision(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, OrderArgs)
        view = await self.port.deal(self.vendor_id, args.order_id, self.session.language)
        say = view.readback
        if view.deal.status == "awaiting_consent":
            self.session.pending = Pending(
                "deal", view.deal.order_id, view.subject_hash, view.readback
            )
            say = f"{say} {self.strings.t('readback.consent_question')}"
            self.events.append({"type": "consent_needed", "data": {"order_id": args.order_id}})
        return {"say": say, "status": view.deal.status}

    async def capture_consent(self, args: BaseModel) -> dict[str, Any]:
        """Consent uses the vendor's own last words, never text the model supplies."""
        assert isinstance(args, ConsentArgs)
        pending = self.session.pending
        if pending is None or (pending.kind, pending.subject_id) != (args.subject, args.subject_id):
            return {"error": "no_readback", "say": self.strings.t("chat.nothing_to_confirm")}
        request = ConsentRequest(
            subject_hash=pending.subject_hash,
            transcript=self.session.last_user_text,
            language=self.session.language,
        )
        if pending.kind == "deal":
            view = await self.port.consent_deal(self.vendor_id, pending.subject_id, request)
            self.events.append({"type": "deal", "data": view.model_dump(mode="json")})
        else:
            mandate = await self.port.consent_rules(self.vendor_id, request)
            self.events.append({"type": "rules", "data": mandate.model_dump(mode="json")})
        self.session.pending = None
        return {"say": self.strings.t("chat.signed")}

    async def decline(self, _: BaseModel) -> dict[str, Any]:
        pending = self.session.pending
        if pending is not None and pending.kind == "deal":
            await self.port.decline_deal(self.vendor_id, pending.subject_id)
        self.session.pending = None
        return {"say": self.strings.t("chat.cancelled")}

    # ---- rules ------------------------------------------------------------------------------

    async def show_rules(self, _: BaseModel) -> dict[str, Any]:
        view = await self.port.rules(self.vendor_id, self.session.language)
        if view.mandate is None:
            return {"say": self.strings.t("chat.rules_none")}
        return {"say": rules_readback(view.mandate, self.strings)}

    async def draft_rules(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, RulesDraft)
        view = await self.port.draft_rules(self.vendor_id, args)
        assert view.subject_hash is not None
        self.session.pending = Pending("rules", self.vendor_id, view.subject_hash, view.readback)
        self.events.append({"type": "consent_needed", "data": {"rules": True}})
        return {"say": f"{view.readback} {self.strings.t('readback.consent_question')}"}

    # ---- listings & onboarding ---------------------------------------------------------------

    async def draft_listing(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, ListingArgs)
        sub = ListingSubmission(
            vendor_id=self.vendor_id,
            language=self.session.language,
            transcript=args.spoken_text,
            photo_b64=self.session.last_photo_b64,
            photo_mime=self.session.last_photo_mime,
        )
        view = await self.port.draft_listing(self.vendor_id, sub)
        listing = view.result.listing
        self.events.append(
            {"type": "listing", "data": {"listing_id": listing.id if listing else None}}
        )
        return {
            "say": view.readback,
            "listing_id": listing.id if listing else None,
            "needs_review": view.result.needs_review,
            "flags": [f.code for f in view.result.flags],
        }

    async def confirm_listing(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, ListingIdArgs)
        ack = await self.port.confirm_listing(self.vendor_id, args.listing_id)
        return {"ok": ack.ok, "detail": ack.detail}

    async def submit_document(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, DocumentArgs)
        doc = await self.port.submit_document(
            self.vendor_id,
            DocumentUpload(
                doc_type=args.doc_type,
                number=args.number,
                data_b64=self.session.last_photo_b64,
                content_type=self.session.last_photo_mime,
                filename="spoken-document",
            ),
        )
        return {"status": doc.status, "failure_reason": doc.failure_reason}

    async def save_profile_field(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, ProfileArgs)
        await self.port.update_profile(
            self.vendor_id, ProfileUpdate.model_validate({args.field: args.value})
        )
        return {"ok": True}

    # ---- questions agents are waiting on --------------------------------------------------------

    async def list_questions(self, _: BaseModel) -> dict[str, Any]:
        """Read the next open question (consent is handled on the deal, not here)."""
        open_ = [q for q in await self.port.questions(self.vendor_id) if q.kind != "consent"]
        if not open_:
            return {"say": self.strings.t("chat.no_questions"), "count": 0}
        first = open_[0]
        self.session.pending = Pending("question", first.id, "", first.question)
        hint = ""
        if first.request.choices:
            hint = " " + self.strings.t(
                "hitl.msg.reply_hint", choices=" / ".join(c.label for c in first.request.choices)
            )
        return {"say": f"{first.question}{hint}", "task_id": first.id, "count": len(open_)}

    async def answer_question(self, args: BaseModel) -> dict[str, Any]:
        """Like consent: the answer is her own last words, never text the model supplies."""
        assert isinstance(args, QuestionArgs)
        pending = self.session.pending
        if pending is None or (pending.kind, pending.subject_id) != ("question", args.task_id):
            return {"error": "no_question_read", "say": self.strings.t("chat.no_questions")}
        task = await self.port.answer_question(
            self.vendor_id, args.task_id, self.session.last_user_text
        )
        self.session.pending = None
        self.events.append({"type": "notice", "data": {"answered": task.id}})
        return {"say": self.strings.t("chat.answered")}

    # ---- activity ---------------------------------------------------------------------------

    async def what_did_you_do_today(self, _: BaseModel) -> dict[str, Any]:
        items = await self.port.today(self.vendor_id, self.session.language)
        if not items:
            return {"say": self.strings.t("chat.today_none")}
        return {"say": self.strings.t("chat.today", items="; ".join(items))}


def deal_summary(view: DealView) -> str:
    """A deal as she would name it: what was ordered (latest version), not its id."""
    latest = view.deal.versions[-1] if view.deal.versions else None
    if latest is None or not latest.lines:
        return view.deal.order_id
    return ", ".join(f"{line.qty} {line.unit} {line.sku}" for line in latest.lines)
