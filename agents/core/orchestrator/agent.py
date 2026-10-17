"""The Retailer Orchestrator agent.

Workflows come from config (`config/workflows/`); specialists are found only by capability via
the registry, filtered by the ActiveConfig. Adding a specialist never changes this file.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from agents.base import Agent, skill

if TYPE_CHECKING:
    from agents.registry import AgentRegistry
from vl_core.errors import Problem
from vl_core.registry import RegistryError
from vl_core.schemas import (
    ActiveConfig,
    AgentCard,
    BuildListingIn,
    BuildListingOut,
    CounterReviewIn,
    CounterReviewOut,
    ListingReviewIn,
    ListingReviewOut,
    ListingStepOut,
    OnboardIn,
    OnboardOut,
    OrderPlanOut,
    PlanOrdersIn,
    PlanOrdersOut,
    PolicyGate,
    TermsIn,
    TermsJobIn,
    TermsOut,
    VendorCheckOut,
)


class Orchestrator(Agent):
    def _registry(self) -> AgentRegistry:
        if self.ctx.registry is None:
            raise Problem(500, "no_registry", "the orchestrator needs an agent registry")
        return self.ctx.registry

    def steps(self, active: ActiveConfig, job_type: str) -> list[tuple[AgentCard, str]]:
        """Resolve a workflow to (agent card, capability) pairs for the active config."""
        workflow = active.workflows.get(job_type)
        if workflow is None:
            raise Problem(409, "no_workflow", job_type)
        resolved = []
        for step in workflow.steps:
            found = self._registry().find_all(step.capability, active)
            if not found and not step.optional:
                raise Problem(
                    409,
                    "capability_not_loaded",
                    f"{job_type} needs {step.capability}; not in the active config",
                )
            if len(found) > 1:
                raise Problem(
                    409, "capability_ambiguous", f"{step.capability}: {[c.agent_id for c in found]}"
                )
            resolved += [(card, step.capability) for card in found]
        return resolved

    async def _run[T: BaseModel](
        self, card: AgentCard, capability: str, payload: BaseModel, out: type[T]
    ) -> T:
        from agents.registry import AgentBlocked

        try:
            result = await self._registry().invoke(card, capability, payload)
        except AgentBlocked:
            raise  # the control plane stopped it: surface that, not a generic failure
        except RegistryError as exc:
            raise Problem(502, "specialist_failed", f"{card.agent_id}: {exc}") from exc
        return out.model_validate(result.model_dump())

    @skill("orchestrator.build_listing")
    async def build_listing(self, inp: BuildListingIn) -> BuildListingOut:
        step_in = inp.step_in
        out = ListingStepOut(listing=None)
        ran: list[str] = []
        for card, capability in self.steps(inp.active_config, "build_listing"):
            result = await self._run(card, capability, step_in, ListingStepOut)
            ran.append(card.agent_id)
            out = ListingStepOut(
                listing=result.listing or out.listing,
                flags=[*out.flags, *result.flags],
                needs_review=out.needs_review or result.needs_review,
                notes=" ".join(n for n in (out.notes, result.notes) if n),
                requests=[*out.requests, *result.requests],
            )
            step_in = step_in.model_copy(update={"listing": out.listing})
        return BuildListingOut(result=out, ran=ran)

    @skill("orchestrator.onboard_vendor")
    async def onboard_vendor(self, inp: OnboardIn) -> OnboardOut:
        out = VendorCheckOut(passed=True)
        ran: list[str] = []
        for card, capability in self.steps(inp.active_config, "onboard_vendor"):
            r = await self._run(card, capability, inp.check_in, VendorCheckOut)
            ran.append(card.agent_id)
            out = VendorCheckOut(
                checks=[*out.checks, *r.checks],
                flags=[*out.flags, *r.flags],
                passed=out.passed and r.passed,
                requests=[*out.requests, *r.requests],
            )
        status: Literal["approved", "pending_approval", "checks_failed"]
        if not out.passed:
            status = "checks_failed"
        elif inp.active_config.retailer_policy.new_vendor_needs_human:
            status = "pending_approval"
        else:
            status = "approved"
        return OnboardOut(result=out, ran=ran, status=status)

    @skill("orchestrator.plan_orders")
    async def plan_orders(self, inp: PlanOrdersIn) -> PlanOrdersOut:
        steps = self.steps(inp.active_config, "place_order")
        if not steps:
            raise Problem(409, "capability_not_loaded", "no ordering specialist loaded")
        card, capability = steps[0]
        plan = await self._run(card, capability, inp.plan_in, OrderPlanOut)
        limit = inp.active_config.retailer_policy.auto_order_max_value
        gates = [
            PolicyGate(
                order_id=p.order_id,
                auto=p.total_value() <= limit,
                reason=""
                if p.total_value() <= limit
                else f"value {p.total_value()} over auto-order limit {limit}",
            )
            for p in plan.proposals
        ]
        return PlanOrdersOut(plan=plan, gates=gates, ran=[card.agent_id])

    @skill("orchestrator.review_counter")
    async def review_counter(self, inp: CounterReviewIn) -> CounterReviewOut:
        base = max(inp.original.total_value(), Decimal(1))
        delta = float(abs(inp.counter.total_value() - inp.original.total_value()) / base)
        limit = inp.active_config.retailer_policy.auto_accept_counter_max_delta
        if delta <= limit:
            return CounterReviewOut(accept=True, delta=delta)
        return CounterReviewOut(
            accept=False,
            delta=delta,
            reason=f"counter changes value by {delta:.0%} (limit {limit:.0%})",
        )

    @skill("orchestrator.propose_terms")
    async def propose_terms(self, inp: TermsJobIn) -> TermsOut:
        steps = self.steps(inp.active_config, "propose_terms")
        card, capability = steps[0]
        return await self._run(
            card,
            capability,
            TermsIn(
                retailer_id=inp.retailer_id,
                vendor_id=inp.vendor_id,
                retailer_profile=inp.active_config.retailer_profile,
                template_id=inp.template_id,
            ),
            TermsOut,
        )

    @skill("orchestrator.review_listing")
    async def review_listing(self, inp: ListingReviewIn) -> ListingReviewOut:
        reasons = [f.code for f in inp.listing.flags]
        if not inp.vendor_approved:
            reasons.append("VENDOR_NOT_APPROVED")
        threshold = inp.active_config.retailer_policy.listing_publish_min_confidence
        if inp.listing.confidence < threshold:
            reasons.append(f"CONFIDENCE_BELOW_{threshold}")
        return ListingReviewOut(publish=not reasons, reasons=reasons)
