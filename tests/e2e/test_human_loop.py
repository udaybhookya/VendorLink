"""Human in the loop across services: agents ask people on in-app / Slack / Gmail, the answers
resume the waiting work. Slack and Gmail go to the local outbox here (VL_CHANNELS=local)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.resources import CLOCK
from scripts._demo import (
    MAYA,
    RETAILER,
    call,
    day,
    deal,
    outbox,
    place,
    reply,
    reset,
    set_language,
    tick,
)
from scripts._stack import APPS
from vl_core.settings import Settings


async def set_limit(s: Settings, limit: str) -> None:
    body = await call(s.retailer_svc_url, "GET", "/config/retailer_policies/default_gifting")
    await call(
        s.retailer_svc_url,
        "PUT",
        "/config/retailer_policies/default_gifting",
        json={**body, "auto_order_max_value": limit},
    )
    await call(
        s.retailer_svc_url, "POST", f"/retailers/{RETAILER}/config/activate", json={"preset": "A"}
    )


async def test_buyer_approves_on_slack_and_the_order_goes(stack: Settings) -> None:
    await reset(stack, "A")
    await set_limit(stack, "1000")
    order = await place(stack, "store:hub-7", day(1))
    assert order["status"] == "queued"
    [msg] = [m for m in await outbox(stack) if m["message"]["to"] == "#freshcart-buying"]
    assert "over the auto-order limit" in msg["message"]["text"]
    await reply(stack, msg, "👍 approve")
    assert (await tick(stack))["answered"]
    orders = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/orders")
    assert next(o for o in orders if o["order_id"] == order["order_id"])["status"] == "confirmed"
    tasks = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/tasks")
    assert tasks[0]["answered_via"] == "slack" and tasks[0]["answered_by"] == "U-asha"


async def test_deciding_in_the_console_stops_the_slack_ask(stack: Settings) -> None:
    await reset(stack, "A")
    await set_limit(stack, "1000")
    order = await place(stack, "store:hub-7", day(1))
    queue = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/queue")
    item = next(q for q in queue if q["ref_id"] == order["order_id"])
    await call(stack.retailer_svc_url, "POST", f"/retailers/{RETAILER}/queue/{item['id']}/reject")
    task = next(
        t
        for t in await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/tasks")
        if t["id"] == item["task_id"]
    )
    assert task["status"] == "answered" and "console" in task["answer"]


async def test_batch_run_pauses_for_people_and_resumes(stack: Settings) -> None:
    await reset(stack, "A")
    await set_language(stack, "te")  # questions reach her in the language she chose
    await set_limit(stack, "10000")  # the gifting desk (50 × 220 = 11000) needs the buyer
    run = await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/jobs/replenishment-run",
        json={"for_date": day(2).isoformat(), "vendor_id": MAYA},
    )
    assert run["status"] == "waiting" and len(run["waiting_on"]) == 1
    sent = {o["store"]: o["status"] for o in run["summary"]["orders"]}
    assert sent["store:hub-7"] == "confirmed"  # within policy: not held behind the buyer
    assert run["summary"]["asked_vendor"] == ["rose candle"]

    # Maya is asked about rose candles in the app, in Telugu; her answer goes back to FreshCart.
    questions = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
    rose = next(q for q in questions if q["request"]["field"] == "supply:rose candle")
    assert "rose candle" in rose["question"] and rose["language"] == "te"
    # she switches to English: the same open question now reads in English
    await set_language(stack, "en")
    again = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
    english = next(q for q in again if q["id"] == rose["id"])
    assert english["language"] == "en" and "can you supply rose candle" in english["question"]
    await set_language(stack, "te")
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/questions/{rose['id']}/answer",
        json={"text": "20"},
    )
    answers = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/info-responses")
    assert answers[0]["answer_value"] == "20" and answers[0]["vendor_id"] == MAYA

    # The buyer approves the gifting desk's order on Slack, hours later; the job resumes.
    msg = next(
        m
        for m in await outbox(stack)
        if "gifting desk" in m["message"]["text"] and "auto-order limit" in m["message"]["text"]
    )
    await reply(stack, msg, "approve")
    await tick(stack)
    run = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/jobs/{run['id']}")
    assert run["status"] == "done"
    hub4 = next(o for o in run["summary"]["orders"] if o["store"] == "store:hub-4")
    # 50 candles @ 08:00 on 45 days comes back red; Hub 7's signed 10 already used part of
    # Maya's 40 for the day, so the counter (30) is past auto-accept: the buyer decides again.
    assert hub4["status"] == "queued"
    if hub4["status"] == "queued":
        assert any("countered order" in m["message"]["text"] for m in await outbox(stack))
    texts = [m["message"]["text"] for m in await outbox(stack)]
    assert any("Order run" in t for t in texts)  # ops told on Slack


async def test_consent_is_never_answered_by_text(stack: Settings) -> None:
    await reset(stack, "A")
    order = await place(stack, "store:hub-9", day(3))  # 30 candles: needs her yes
    questions = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
    consent = next(q for q in questions if q["kind"] == "consent")
    assert consent["subject_ref"] == order["order_id"]
    r = await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/questions/{consent['id']}/answer",
        expect=409,
        json={"text": "yes"},
    )
    assert r["detail"]["error"] == "answer_in_deals"
    # Saying yes on the deal (after the read-back) signs and closes the question.
    view = await deal(stack, order["order_id"])
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/deals/{order['order_id']}/consent",
        json={"subject_hash": view["subject_hash"], "transcript": "అవును", "language": "te"},
    )
    assert not [
        q
        for q in await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
        if q["kind"] == "consent"
    ]


async def test_missing_listing_details_are_asked_and_the_listing_completes(stack: Settings) -> None:
    await reset(stack, "A")
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/listings/draft",
        json={"vendor_id": MAYA, "language": "te", "transcript": "I have new rose candles"},
    )
    [q] = [
        q
        for q in await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
        if q["request"]["field"] == "describe"
    ]
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/questions/{q['id']}/answer",
        json={"text": "20 piece of rose candle at 210"},
    )
    listings = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/listings")
    rose = next(item for item in listings if item["sku_name"] == "rose candle")
    assert rose["price"] == "210" and rose["qty"] == "20"


async def test_missing_document_is_asked_then_application_reruns(stack: Settings) -> None:
    await reset(stack, "A")
    vendor = await call(
        stack.vendor_svc_url,
        "POST",
        "/vendors",
        json={
            "display_name": "Meena",
            "legal_name": "Maya Thomas",
            "business_profile": "handmade_gifts",
            "country": "IN",
            "language": "en",
            "email": "meena@example.com",
        },
    )
    vid = vendor["id"]
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{vid}/documents",
        json={"doc_type": "aadhaar", "number": "234567890123"},
    )
    first = await call(
        stack.vendor_svc_url, "POST", f"/vendors/{vid}/apply", params={"retailer_id": RETAILER}
    )
    assert not first["passed"]
    [q] = await call(stack.vendor_svc_url, "GET", f"/vendors/{vid}/questions")
    assert q["request"]["field"] == "doc:ifsc_account"
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{vid}/questions/{q['id']}/answer",
        json={"text": "SBIN0001234:123456789012"},
    )
    me = await call(stack.vendor_svc_url, "GET", f"/vendors/{vid}/me")
    assert me["status"] == "pending_approval"  # re-applied by itself and passed
    # …and the category manager is now asked on Slack.
    assert any(m["message"]["to"] == "#freshcart-catalogue" for m in await outbox(stack))


async def test_unanswered_consent_expires_and_closes_the_order(stack: Settings) -> None:
    await reset(stack, "A")
    order = await place(stack, "store:hub-9", day(3))
    assert (await deal(stack, order["order_id"]))["deal"]["status"] == "awaiting_consent"
    later = datetime.now(UTC) + timedelta(hours=7)
    APPS["vendor"].state.operations.agents.resources[CLOCK] = lambda: later
    try:
        await tick(stack)
    finally:
        APPS["vendor"].state.operations.agents.resources.pop(CLOCK)
    assert (await deal(stack, order["order_id"]))["deal"]["status"] == "declined"
    orders = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/orders")
    assert next(o for o in orders if o["order_id"] == order["order_id"])["status"] == "declined"


async def test_concierge_reads_questions_and_takes_her_spoken_answer(stack: Settings) -> None:
    await reset(stack, "A")
    await set_language(stack, "te")
    await call(
        stack.vendor_svc_url,
        "POST",
        f"/vendors/{MAYA}/listings/draft",
        json={"vendor_id": MAYA, "language": "te", "transcript": "I have new rose candles"},
    )
    chat = f"/vendors/{MAYA}/chat"
    asked = await call(stack.vendor_svc_url, "POST", chat, json={"text": "నాకు ఏమైనా ప్రశ్నలు ఉన్నాయా?"})
    assert asked["mode"] == "offline" and "ఏమి అమ్ముతున్నారు" in asked["reply"]
    done = await call(
        stack.vendor_svc_url, "POST", chat, json={"text": "20 piece of rose candle at 210"}
    )
    assert "పంపాను" in done["reply"]
    listings = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/listings")
    assert any(item["sku_name"] == "rose candle" and item["price"] == "210" for item in listings)
    after = await call(stack.vendor_svc_url, "POST", chat, json={"text": "ప్రశ్నలు?"})
    assert "ఎవరూ ఎదురుచూడటం లేదు" in after["reply"]


async def test_demo_scenes_leave_work_waiting_on_people(stack: Settings) -> None:
    from scripts._demo import SCENES

    await reset(stack, "A")
    run = await SCENES["waiting"][1](stack)
    assert run["status"] == "waiting" and len(run["waiting_on"]) == 1
    questions = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/questions")
    assert any(q["request"]["field"] == "supply:rose candle" for q in questions)
    assert any("auto-order limit" in m["message"]["text"] for m in await outbox(stack))
    [terms] = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/terms")
    assert terms["status"] == "proposed"

    await reset(stack, "B")
    listing = await SCENES["sara"][1](stack)
    codes = {f["code"] for f in listing["result"]["flags"]}
    assert "MISSING_REGISTRATION_PIRT" in codes


async def test_screens_rereading_tasks_never_block_the_liaison(stack: Settings) -> None:
    """A console re-reads tasks on every event. A burst of reads (a reset, a batch run) must not
    block the Liaison's real work or get the retailer service's host credential revoked."""
    from scripts._demo import SCENES

    await reset(stack, "A")
    await SCENES["waiting"][1](stack)
    for _ in range(200):
        await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/tasks")
    tasks = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/tasks")
    task = next(t for t in tasks if t["status"] == "open" and t["role"] == "buyer")
    answered = await call(
        stack.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/tasks/{task['id']}/answer",
        json={"text": "approve", "by": "console"},
    )
    assert answered["status"] == "answered"
    await tick(stack)
    [run] = await call(stack.retailer_svc_url, "GET", f"/retailers/{RETAILER}/jobs")
    assert run["status"] == "done"
    creds = await call(stack.trust_gateway_url, "GET", "/agents")
    assert all(c["status"] == "active" for c in creds if c["agent_id"].startswith("host:"))


async def test_english_by_default_and_she_can_switch_language(stack: Settings) -> None:
    await reset(stack, "A")
    me = await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/me")
    assert me["language"] == "en"
    chat = f"/vendors/{MAYA}/chat"
    hello = await call(stack.vendor_svc_url, "POST", chat, json={"text": "deals"})
    assert hello["language"] == "en"
    await set_language(stack, "te")
    assert (await call(stack.vendor_svc_url, "GET", f"/vendors/{MAYA}/me"))["language"] == "te"
    # Bahasa is not offered in India's Country Pack
    bad = await call(
        stack.vendor_svc_url,
        "PATCH",
        f"/vendors/{MAYA}/profile",
        json={"language": "id"},
        expect=422,
    )
    assert bad["detail"]["error"] == "unsupported_language"
