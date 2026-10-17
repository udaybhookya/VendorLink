"""The Vendor Guardian: the only agent that signs. No LLM here, ever (CLAUDE.md rule 3)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel

from agents.base import Agent, AgentContext, skill
from agents.core.guardian.rules import evaluate, pickup_date
from vl_core.a2a import agent_id_for
from vl_core.crypto import b64d, b64e, canonical_json, hash_obj, verify_signature
from vl_core.errors import Problem
from vl_core.schemas import (
    AgentCard,
    ConsentRecord,
    DecideIn,
    Decision,
    DecisionStatus,
    EvaluateIn,
    MandateOut,
    MandateQuery,
    OrderProposal,
    SignMandateIn,
    SignOrderIn,
    SignOrderOut,
    StandingMandate,
)
from vl_core.schemas.common import Dec


class SigningRefused(Problem):
    def __init__(self, code: str, decision: Decision | None = None) -> None:
        super().__init__(409, "guardian_refused", code)
        self.code_reason = code
        self.decision = decision


class Commitment(BaseModel):
    """Quantities one signed order commits on its local pickup date."""

    order_id: str
    vendor_id: str
    day: date
    qty: dict[str, Dec]


def consent_matches(consent: ConsentRecord | None, vendor_id: str, subject: object) -> bool:
    return (
        consent is not None
        and consent.vendor_id == vendor_id
        and consent.subject_hash == hash_obj(subject)
    )


class Guardian(Agent):
    def __init__(self, card: AgentCard, ctx: AgentContext, options: BaseModel | None = None):
        super().__init__(card, ctx, options)
        self._mandates = ctx.repo(StandingMandate, "mandates")
        self._commitments = ctx.repo(Commitment, "commitments")
        self._lock = asyncio.Lock()

    async def _sign(self, vendor_id: str, data: bytes) -> str:
        if self.ctx.signer is None:
            raise SigningRefused("NO_SIGNING_KEY")
        return b64e(await self.ctx.signer.sign(agent_id_for(vendor_id), data))

    async def committed(
        self, vendor_id: str, day: date, exclude_order: str | None = None
    ) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for c in await self._commitments.query(vendor_id=vendor_id, day=day):
            if c.order_id != exclude_order:
                for sku, qty in c.qty.items():
                    totals[sku] += qty
        return dict(totals)

    async def _decide(self, proposal: OrderProposal) -> tuple[Decision, StandingMandate | None]:
        mandate = await self._mandates.get(proposal.vendor_id)
        committed: dict[str, Decimal] = {}
        if mandate is not None:
            committed = await self.committed(
                proposal.vendor_id, pickup_date(proposal, mandate), exclude_order=proposal.order_id
            )
        return evaluate(proposal, mandate, committed), mandate

    async def _commit(self, proposal: OrderProposal, mandate: StandingMandate | None) -> None:
        if mandate is None:
            return
        qty: dict[str, Decimal] = defaultdict(Decimal)
        for line in proposal.lines:
            qty[line.sku] += line.qty
        await self._commitments.put(
            proposal.order_id,
            Commitment(
                order_id=proposal.order_id,
                vendor_id=proposal.vendor_id,
                day=pickup_date(proposal, mandate),
                qty=dict(qty),
            ),
        )

    # ---- skills -------------------------------------------------------------------------------

    @skill("guardian.evaluate")
    async def evaluate(self, inp: EvaluateIn) -> Decision:
        return evaluate(inp.proposal, inp.mandate, inp.committed)

    @skill("guardian.decide")
    async def decide(self, inp: DecideIn) -> Decision:
        async with self._lock:
            decision, mandate = await self._decide(inp.proposal)
            if decision.status == DecisionStatus.GREEN:
                sig = await self._sign(inp.proposal.vendor_id, canonical_json(inp.proposal))
                await self._commit(inp.proposal, mandate)
                decision = decision.model_copy(update={"vendor_sig": sig})
            return decision

    @skill("guardian.sign_order")
    async def sign_order(self, inp: SignOrderIn) -> SignOrderOut:
        """Sign amber/red only with consent to exactly this version; never sign a decline."""
        async with self._lock:
            decision, mandate = await self._decide(inp.proposal)
            if decision.status == DecisionStatus.DECLINE:
                raise SigningRefused("DECLINED", decision)
            if decision.status != DecisionStatus.GREEN and not consent_matches(
                inp.consent, inp.proposal.vendor_id, inp.proposal
            ):
                raise SigningRefused("CONSENT_REQUIRED", decision)
            sig = await self._sign(inp.proposal.vendor_id, canonical_json(inp.proposal))
            await self._commit(inp.proposal, mandate)
            return SignOrderOut(
                vendor_sig=sig,
                decision=decision.model_copy(
                    update={"vendor_sig": sig, "consent_ref": inp.consent.id}
                ),
            )

    @skill("guardian.sign_mandate")
    async def sign_mandate(self, inp: SignMandateIn) -> StandingMandate:
        unsigned = inp.mandate.unsigned()
        if not consent_matches(inp.consent, inp.mandate.vendor_id, unsigned):
            raise SigningRefused("CONSENT_REQUIRED")
        sig = await self._sign(inp.mandate.vendor_id, canonical_json(unsigned))
        return unsigned.model_copy(
            update={"signature": sig, "consent_ref": inp.consent.id, "signed_at": datetime.now(UTC)}
        )

    @skill("guardian.install_mandate")
    async def install_mandate(self, inp: StandingMandate) -> MandateOut:
        """Only mandates carrying this Guardian's own valid signature can be put in force."""
        if self.ctx.signer is None or not inp.signature:
            raise SigningRefused("UNSIGNED_MANDATE")
        public = await self.ctx.signer.public_key(agent_id_for(inp.vendor_id))
        if not verify_signature(public, canonical_json(inp.unsigned()), b64d(inp.signature)):
            raise SigningRefused("NOT_SIGNED_BY_GUARDIAN")
        await self._mandates.put(inp.vendor_id, inp)
        return MandateOut(mandate=inp)

    @skill("guardian.mandate")
    async def mandate(self, inp: MandateQuery) -> MandateOut:
        return MandateOut(mandate=await self._mandates.get(inp.vendor_id))
