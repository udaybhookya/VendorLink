"""vendor.verify: document numbers → format check → (simulated) registry check.

A missing document, or one that failed for a reason she can fix, becomes a question to her
(`InfoRequest`); her answer re-saves the number and the application runs again."""

from __future__ import annotations

import re

from agents.base import Agent, skill
from vl_core.schemas import (
    FALLBACK_LANGUAGE,
    DocCheck,
    InfoRequest,
    RegistryCheckRequest,
    RegistryCheckResult,
    VendorCheckIn,
    VendorCheckOut,
)

# Failure codes with their own question (config/i18n `hitl.q.doc_failed.<code>`).
FIXABLE = ("BAD_FORMAT", "NOT_FOUND", "NAME_MISMATCH", "EXPIRED")


def doc_name(spec_name: dict[str, str], spec_id: str, language: str) -> str:
    return spec_name.get(language) or spec_name.get(FALLBACK_LANGUAGE, spec_id)


class OnboardingVerifier(Agent):
    async def _registry(self, registry: str, req: RegistryCheckRequest) -> RegistryCheckResult:
        async with self.ctx.http(self.ctx.settings.mocks_url) as client:
            if registry in ("id", "bank"):
                response = await client.post(f"/{registry}/verify", json=req.model_dump())
            else:
                response = await client.get(f"/{registry}/{req.number}", params={"name": req.name})
        response.raise_for_status()
        return RegistryCheckResult.model_validate(response.json())

    @skill("vendor.verify")
    async def verify(self, inp: VendorCheckIn) -> VendorCheckOut:
        specs = {s.id: s for s in [*inp.country_pack.id_docs, *inp.country_pack.food_safety_regs]}
        checks: list[DocCheck] = []
        for role in inp.business_profile.required_docs:
            for spec in (s for s in specs.values() if s.role == role):
                number = inp.doc_numbers.get(spec.id)
                if number is None:
                    continue
                if not re.fullmatch(spec.regex, number.strip()):
                    checks.append(
                        DocCheck(
                            doc_type=spec.id,
                            status="fail",
                            code="BAD_FORMAT",
                            detail=f"does not look like a {spec.name.get('en')}",
                        )
                    )
                    continue
                if spec.registry is None:
                    checks.append(DocCheck(doc_type=spec.id, status="pass", code="FORMAT_OK"))
                    continue
                result = await self._registry(
                    spec.registry,
                    RegistryCheckRequest(
                        doc_type=spec.id, number=number.strip(), name=inp.legal_name
                    ),
                )
                checks.append(
                    DocCheck(
                        doc_type=spec.id,
                        status=result.status,
                        code=result.code,
                        detail=f"{result.detail} (simulated registry)".strip(),
                    )
                )
        roles_seen = {specs[c.doc_type].role for c in checks}
        language = inp.vendor.language
        requests = [
            InfoRequest(
                field=f"doc:{c.doc_type}",
                ask="vendor",
                answer_type="text",
                question_key=f"hitl.q.doc_failed.{c.code}",
                params={"doc": doc_name(specs[c.doc_type].name, c.doc_type, language)},
            )
            for c in checks
            if c.status == "fail" and c.code in FIXABLE
        ]
        for role in inp.business_profile.required_docs:
            if role not in roles_seen:
                checks.append(
                    DocCheck(doc_type=f"role:{role}", status="fail", code="MISSING_DOCUMENT")
                )
                spec = next(s for s in specs.values() if s.role == role)
                name = doc_name(spec.name, spec.id, language)
                requests.append(
                    InfoRequest(
                        field=f"doc:{spec.id}",
                        ask="vendor",
                        answer_type="text",
                        question_key="hitl.q.doc",
                        params={"doc": name},
                    )
                )
        return VendorCheckOut(
            checks=checks, passed=all(c.status == "pass" for c in checks), requests=requests
        )
