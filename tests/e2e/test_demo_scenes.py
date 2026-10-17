"""The demo scenes as a person would run them, through the routes the two apps call: Sam's
onboarding, agents that ask people, config B with Sara, and the control plane (docs/DEMO.md)."""

from __future__ import annotations

from typing import Any

from scripts._demo import MAYA, RETAILER, SARA, SARA_LABEL, SCENES, call, reset, tick
from vl_core.settings import Settings

SAM = "vendor:sam"
OPS = {"X-VL-Operator-Token": "inprocess-operator"}
SAM_BANK = "HDFC0004321:987654321098"  # registered to "S Daniel Traders", not Sam Daniel


async def save_doc(s: Settings, vendor_id: str, doc_type: str, number: str) -> None:
    await call(
        s.vendor_svc_url,
        "POST",
        f"/vendors/{vendor_id}/documents",
        json={"doc_type": doc_type, "number": number},
    )


async def apply(s: Settings, vendor_id: str) -> dict[str, str]:
    out = await call(
        s.vendor_svc_url, "POST", f"/vendors/{vendor_id}/apply", params={"retailer_id": RETAILER}
    )
    return {c["doc_type"]: c["code"] for c in out["checks"]}


async def new_supplier(s: Settings, vendor_id: str) -> dict[str, Any] | None:
    queue = await call(s.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    return next(
        (
            q
            for q in queue
            if q["kind"] == "vendor" and q["ref_id"] == vendor_id and q["status"] == "pending"
        ),
        None,
    )


async def questions(s: Settings, vendor_id: str) -> list[dict[str, Any]]:
    return list(await call(s.vendor_svc_url, "GET", f"/vendors/{vendor_id}/questions"))


async def test_sam_failed_documents_are_explained_and_asked_again(stack: Settings) -> None:
    await reset(stack, "A")
    await save_doc(stack, SAM, "aadhaar", "345678901234")
    await save_doc(stack, SAM, "ifsc_account", SAM_BANK)
    checks = await apply(stack, SAM)
    assert checks["aadhaar"] == "OK" and checks["ifsc_account"] == "NAME_MISMATCH"
    me = await call(stack.vendor_svc_url, "GET", f"/vendors/{SAM}/me")
    assert me["status"] == "verifying"
    assert await new_supplier(stack, SAM) is None  # the retailer sees him only once all pass
    [ask] = await questions(stack, SAM)  # Needs you: fix the bank account
    assert ask["request"]["field"] == "doc:ifsc_account"
    assert "different name" in ask["question"]

    # applying again re-checks the failed number: still NAME_MISMATCH, not "missing", no twin ask
    assert (await apply(stack, SAM))["ifsc_account"] == "NAME_MISMATCH"
    assert [q["id"] for q in await questions(stack, SAM)] == [ask["id"]]

    # she answers with another number that also fails: she is asked again, about the new reason
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{SAM}/questions/{ask['id']}/answer",
        json={"text": "HDFC0009999:111122223333"},
    )
    [again] = await questions(stack, SAM)
    assert again["id"] != ask["id"] and "could not find" in again["question"]

    # an unknown Aadhaar is NOT_FOUND
    await save_doc(stack, SAM, "aadhaar", "999988887777")
    assert (await apply(stack, SAM))["aadhaar"] == "NOT_FOUND"


async def test_the_question_about_a_failed_document_is_in_her_language(stack: Settings) -> None:
    await reset(stack, "A")
    await call(stack.vendor_svc_url, "PATCH", f"/vendors/{SAM}/profile", json={"language": "te"})
    await save_doc(stack, SAM, "aadhaar", "345678901234")
    await save_doc(stack, SAM, "ifsc_account", SAM_BANK)
    await apply(stack, SAM)
    [ask] = await questions(stack, SAM)
    assert "వేరే పేరుతో" in ask["question"]


async def test_agents_ask_people_and_the_run_resumes(stack: Settings) -> None:
    await reset(stack, "A")
    run = await SCENES["waiting"][1](stack)
    sent = {o["store"]: o["status"] for o in run["summary"]["orders"]}
    assert run["status"] == "waiting"
    assert sent["store:hub-7"] == "confirmed" and sent["store:hub-12"] == "confirmed"

    tasks = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/tasks")
    [held] = [t for t in tasks if t["status"] == "open" and t["role"] == "buyer"]
    assert "hub-4" in held["subject_ref"] and "over the auto-order limit" in held["question"]
    await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/tasks/{held['id']}/answer",
        json={"text": "approve", "by": "console"},
    )
    await tick(stack)
    [run] = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/jobs")
    assert run["status"] == "done"
    hub4 = next(o for o in run["summary"]["orders"] if o["store"] == "store:hub-4")
    assert hub4["status"] == "queued"  # her Guardian countered 50 @ 08:00; back to the buyer

    rose = next(
        q for q in await questions(stack, MAYA) if q["request"]["field"] == "supply:rose candle"
    )
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/questions/{rose['id']}/answer",
        json={"text": "20"},
    )
    answers = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/info-responses")
    assert any(a["vendor_id"] == MAYA and a["answer_value"] == "20" for a in answers)

    [terms] = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/terms")
    assert terms["terms"]["payment_days"] == 45 and terms["verdict"] == "PAYMENT_TOO_LATE"
    refused = await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/terms/{terms['terms']['terms_id']}/accept",
        expect=None,
    )
    assert refused["detail"]["error"] == "outside_your_rules"


async def test_config_b_sara_applies_and_her_label_is_checked(stack: Settings) -> None:
    await reset(stack, "A")
    await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/config/activate",
        json={"preset": "B"},
    )
    active = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/config/active")
    loaded = set(active["retailer_profile"]["specialists"]) | set(
        active["business_profile"]["specialists"]
    )
    assert active["country_pack"]["code"] == "ID"
    assert {"label-checker", "compliance-auditor"} <= loaded
    assert "craft-quality-checker" not in loaded

    checks = await apply(stack, SARA)
    assert all(code == "OK" for code in checks.values())
    assert await new_supplier(stack, SARA) is not None

    async def flags(label: str) -> set[str]:
        view = await call(
            stack.vendor_svc_url,
            "POST",
            f"/vendors/{SARA}/listings/draft",
            json={
                "vendor_id": SARA,
                "language": "id",
                "transcript": "spicy cassava chips 100 gram, 200 bungkus, 12000",
                "label_text": label,
            },
        )
        return {f["code"] for f in view["result"]["flags"]}

    without = await flags(SARA_LABEL)
    assert "MISSING_REGISTRATION_PIRT" in without
    assert not any(c.startswith("IMAGE_") for c in without)  # config B has no photo standards
    assert "MISSING_REGISTRATION_PIRT" not in await flags(
        SARA_LABEL + "\nP-IRT No. 206320101014925"
    )


async def test_control_plane_pages_and_suspending_the_terms_agent(stack: Settings) -> None:
    await reset(stack, "A")
    for page in ("overview", "connections", "activities", "agents", "traces", "safety"):
        await call(stack.trust_gateway_url, "GET", f"/observability/{page}")
    await call(stack.trust_gateway_url, "GET", "/observability/agents/vendor-agent:maya")

    who = {"actor": "asha@freshcart", "reason": "demo"}
    await call(
        stack.trust_gateway_url,
        "POST",
        "/control/agents/terms-agent/suspend",
        headers=OPS,
        json=who,
    )
    refused = await call(
        stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/terms/{MAYA}", expect=None
    )
    assert refused["detail"]["error"] == "agent_blocked"
    await call(
        stack.trust_gateway_url,
        "POST",
        "/control/agents/terms-agent/resume",
        headers=OPS,
        json=who,
    )
    proposed = await call(stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/terms/{MAYA}")
    assert proposed["terms"]["payment_days"] == 45
