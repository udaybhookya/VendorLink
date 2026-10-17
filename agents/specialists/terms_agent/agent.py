"""terms.propose: fill the retailer's term template for one vendor. No LLM."""

from __future__ import annotations

import uuid

from agents.base import Agent, skill
from vl_core.schemas import TermsIn, TermsOut, TermsProposal


class TermsAgent(Agent):
    @skill("terms.propose")
    async def propose(self, inp: TermsIn) -> TermsOut:
        templates = inp.retailer_profile.term_templates
        template = next((t for t in templates if t.id == inp.template_id), None) or templates[0]
        return TermsOut(
            terms=TermsProposal(
                terms_id=f"terms-{uuid.uuid4().hex[:10]}",
                retailer_id=inp.retailer_id,
                vendor_id=inp.vendor_id,
                template_id=template.id,
                payment_days=template.payment_days,
                description=template.description,
            )
        )
