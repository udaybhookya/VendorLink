"""A → B with zero code change: different specialists run, different flags come back."""

from __future__ import annotations

from scripts._demo import RETAILER, call, reset, use_terms
from vl_core.settings import Settings

SARA = "vendor:sara"
LABEL = "Spicy Cassava Chips\nKomposisi: singkong, minyak sawit, cabai, garam\nBerat bersih 100 g\nBaik digunakan sebelum 12/2026"  # noqa: E501


async def submit(
    s: Settings, vendor: str, transcript: str, label: str | None, language: str
) -> dict:  # type: ignore[type-arg]
    return dict(
        await call(
            s.vendor_svc_url,
            "POST",
            f"/vendors/{vendor}/listings/draft",
            json={
                "vendor_id": vendor,
                "language": language,
                "transcript": transcript,
                "label_text": label,
            },
        )
    )


async def test_switch_a_to_b_changes_specialists_and_flags(stack: Settings) -> None:
    await reset(stack, "A")
    active_a = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/config/active")
    assert "craft-quality-checker" in active_a["specialists"]

    # Under A, Maya's spoken listing goes to the catalogue builder and the craft quality checker.
    a = await submit(stack, "vendor:maya", "20 piece of rose candle at 210", None, "te")
    codes_a = {f["code"] for f in a["result"]["flags"]}
    assert "NO_PHOTO" in codes_a and "PARSED_WITHOUT_MODEL" in codes_a

    # Switch to B live; the gateway records the new config hash.
    active_b = await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/config/activate",
        json={"preset": "B"},
    )
    assert active_b["config_hash"] != active_a["config_hash"]
    assert "label-checker" in active_b["specialists"]
    audit = await call(stack.trust_gateway_url, "GET", "/audit")
    assert any(
        r["action"] == "config.activate" and r["config_hash"] == active_b["config_hash"]
        for r in audit
    )

    # Sara applies under B: ID, bank and P-IRT pass the simulated registries → needs approval.
    check = await call(stack.vendor_svc_url, "POST", f"/vendors/{SARA}/apply")
    assert check["passed"], check
    queue = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    assert any(q["kind"] == "vendor" and q["ref_id"] == SARA for q in queue)

    # Her snack label has no P-IRT number: the Label Checker flags it.
    b = await submit(stack, SARA, "50 pack of cassava chips at 15000", LABEL, "id")
    codes_b = {f["code"] for f in b["result"]["flags"]}
    assert "MISSING_REGISTRATION_PIRT" in codes_b
    assert "NO_PHOTO" not in codes_b  # the craft quality checker did not run under B
    listing = b["result"]["listing"]
    assert listing["currency"] == "IDR" and listing["attributes"]["ingredients"]

    # With the P-IRT number on the label, that flag goes away.
    fixed = await submit(
        stack,
        SARA,
        "50 pack of cassava chips at 15000",
        LABEL + "\nP-IRT No. 206320101014925",
        "id",
    )
    assert "MISSING_REGISTRATION_PIRT" not in {f["code"] for f in fixed["result"]["flags"]}


async def test_approval_queue_activates_vendor(stack: Settings) -> None:
    await reset(stack, "B")
    await call(stack.vendor_svc_url, "POST", f"/vendors/{SARA}/apply")
    queue = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    item = next(q for q in queue if q["ref_id"] == SARA)
    await call(stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/queue/{item['id']}/approve")
    me = await call(stack.vendor_svc_url, "GET", f"/vendors/{SARA}/me")
    assert me["status"] == "active"


async def test_order_over_policy_limit_is_queued(stack: Settings) -> None:
    await reset(stack, "A")
    body = await call(stack.retailer_svc_url, "GET", "/config/retailer_policies/default_gifting")
    await call(
        stack.retailer_svc_url,
        "PUT",
        "/config/retailer_policies/default_gifting",
        json={**body, "auto_order_max_value": "1000"},
    )
    await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/config/activate",
        json={"preset": "A"},
    )
    from scripts._demo import day, place

    order = await place(stack, "store:hub-7", day(1))
    assert order["status"] == "queued"
    queue = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    item = next(q for q in queue if q["ref_id"] == order["order_id"])
    await call(stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/queue/{item['id']}/approve")
    orders = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/orders")
    assert next(o for o in orders if o["order_id"] == order["order_id"])["status"] == "confirmed"


async def test_concierge_offline_reads_back_and_takes_consent(stack: Settings) -> None:
    await reset(stack, "A")
    from scripts._demo import day, place, set_language

    await set_language(stack, "te")  # she switches the app to Telugu
    order = await place(stack, "store:hub-4", day(2))
    # the 50-candle order comes back red; its counter (40 @ 10:00, 15 days) waits on the buyer
    queue = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    item = next(q for q in queue if q["ref_id"] == order["order_id"])
    await call(stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/queue/{item['id']}/approve")
    chat = "/vendors/vendor:maya/chat"
    reply = await call(stack.vendor_svc_url, "POST", chat, json={"text": "నా డీల్స్ ఏమిటి?"})
    assert reply["mode"] == "offline"
    assert "40" in reply["reply"] and any(e["type"] == "consent_needed" for e in reply["events"])
    done = await call(stack.vendor_svc_url, "POST", chat, json={"text": "అవును"})
    assert any(e["type"] == "deal" for e in done["events"]), done
    orders = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/orders")
    assert next(o for o in orders if o["order_id"] == order["order_id"])["status"] == "confirmed"
    today = await call(stack.vendor_svc_url, "POST", chat, json={"text": "ఈరోజు ఏం చేశావు?"})
    assert "సంతకం" in today["reply"]


async def test_terms_reach_the_vendor_and_her_answer_comes_back(stack: Settings) -> None:
    await reset(stack, "A")
    await use_terms(stack, "gift-standard-v1")
    proposed = await call(
        stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/terms/vendor:maya"
    )
    assert proposed["accepted"] and proposed["detail"] == "WITHIN_RULES"
    [offered] = await call(stack.vendor_svc_url, "GET", "/vendors/vendor:maya/terms")
    assert offered["status"] == "proposed" and offered["terms"]["payment_days"] == 15
    tid = offered["terms"]["terms_id"]
    done = await call(stack.vendor_svc_url, "POST", f"/vendors/vendor:maya/terms/{tid}/accept")
    assert done["status"] == "accepted"
    [answer] = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/terms-responses")
    assert answer["accepted"] and answer["terms_id"] == tid
    again = await call(
        stack.vendor_svc_url, "POST", f"/vendors/vendor:maya/terms/{tid}/decline", expect=409
    )
    assert again["detail"]["error"] == "already_answered"


async def test_terms_outside_her_rules_cannot_be_accepted(stack: Settings) -> None:
    await reset(stack, "A")  # corporate gifting pays in 45 days; Maya allows 15
    proposed = await call(
        stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/terms/vendor:maya"
    )
    assert not proposed["accepted"] and proposed["detail"] == "PAYMENT_TOO_LATE"
    [offered] = await call(stack.vendor_svc_url, "GET", "/vendors/vendor:maya/terms")
    r = await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/vendor:maya/terms/{offered['terms']['terms_id']}/accept",
        expect=409,
    )
    assert r["detail"]["error"] == "outside_your_rules"
