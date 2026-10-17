"""Property: the Guardian agent never signs outside the mandate without matching consent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agents.core.guardian.agent import Guardian, SigningRefused
from agents.core.guardian.rules import evaluate
from agents.registry import AgentRegistry
from tests.conftest import CONFIG_DIR
from tests.unit.guardian.fixtures import maya_mandate, proposal
from vl_core.crypto import LocalSigner, b64d, canonical_json, hash_obj, verify_signature
from vl_core.schemas import (
    AgentEntry,
    ConsentRecord,
    DecideIn,
    DecisionStatus,
    OrderProposal,
    SignMandateIn,
    SignOrderIn,
    StandingMandate,
    TimeWindow,
)
from vl_core.settings import Settings
from vl_core.store import reset_memory_repos

AGENT = "vendor-agent:maya"


def oracle_within(p: OrderProposal, m: StandingMandate) -> bool:
    """Independent restatement of the mandate (fresh day, nothing committed)."""
    local = p.pickup_at.astimezone(ZoneInfo(m.timezone)).time().replace(tzinfo=None)
    rules = {r.sku: r for r in m.skus}
    totals: dict[str, Decimal] = {}
    for line in p.lines:
        rule = rules.get(line.sku)
        if rule is None or rule.unit != line.unit or line.price < rule.price_floor:
            return False
        totals[line.sku] = totals.get(line.sku, Decimal(0)) + line.qty
    return (
        p.retailer_id in m.approved_buyers
        and p.currency == m.currency
        and all(totals[s] <= rules[s].max_qty_per_day for s in totals)
        and any(w.start <= local <= w.end for w in m.pickup_windows)
        and p.payment_days <= m.payment_days_max
    )


def consent(subject: object, vendor: str = "vendor:maya") -> ConsentRecord:
    return ConsentRecord(
        id=uuid.uuid4().hex,
        vendor_id=vendor,
        subject_hash=hash_obj(subject),
        transcript="yes",
        language="en",
        captured_at=datetime.now(UTC),
    )


async def guardian_with(mandate: StandingMandate, signer: LocalSigner | None = None) -> Guardian:
    """A fresh Guardian agent (via the registry) with `mandate` signed and in force."""
    reset_memory_repos()
    registry = AgentRegistry(Settings(vl_config_dir=CONFIG_DIR), signer=signer or LocalSigner())
    registry.register(AgentEntry(id="guardian", card="agents/core/guardian/agent.json"))
    g = cast(Guardian, registry.agent("guardian"))
    unsigned = mandate.unsigned()
    signed = await g.sign_mandate(SignMandateIn(mandate=unsigned, consent=consent(unsigned)))
    await g.install_mandate(signed)
    return g


proposals = st.builds(
    proposal,
    qty=st.integers(1, 600),
    at=st.sampled_from(["04:00", "05:30", "05:59", "06:00", "07:15", "10:00", "10:01", "23:59"]),
    price=st.integers(0, 80),
    days=st.integers(0, 30),
    buyer=st.sampled_from(["retailer:freshcart", "retailer:other"]),
    sku=st.sampled_from(["spinach", "tomato", "mint"]),
    unit=st.sampled_from(["bunch", "kg"]),
    currency=st.sampled_from(["INR", "IDR"]),
)
mandates = st.builds(
    lambda mx, floor, start, auto: maya_mandate(
        pickup_windows=[TimeWindow(start=time(start), end=time(start + 4))],
        auto_accept=maya_mandate().auto_accept.model_copy(update={"max_value": Decimal(auto)}),
        skus=[
            r.model_copy(update={"max_qty_per_day": Decimal(mx), "price_floor": Decimal(floor)})
            for r in maya_mandate().skus
        ],
    ),
    st.integers(1, 500),
    st.integers(0, 60),
    st.integers(0, 19),
    st.integers(0, 30000),
)
CFG = settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


@CFG
@given(p=proposals, m=mandates)
async def test_signs_without_consent_only_inside_mandate_and_auto_accept(
    p: OrderProposal, m: StandingMandate
) -> None:
    g = await guardian_with(m)
    decision = await g.decide(DecideIn(proposal=p))
    if decision.vendor_sig:
        assert oracle_within(p, m)
        assert p.total_value() <= m.auto_accept.max_value


@CFG
@given(p=proposals, m=mandates, other=proposals)
async def test_consent_for_other_terms_does_not_help(
    p: OrderProposal, m: StandingMandate, other: OrderProposal
) -> None:
    g = await guardian_with(m)
    wrong = consent(other) if hash_obj(other) != hash_obj(p) else consent({"x": 1})
    for c in (wrong, consent(p, vendor="vendor:sara")):
        try:
            await g.sign_order(SignOrderIn(proposal=p, consent=c))
        except SigningRefused:
            continue
        assert oracle_within(p, m) and p.total_value() <= m.auto_accept.max_value


@CFG
@given(p=proposals, m=mandates)
async def test_matching_consent_signs_everything_but_declines(
    p: OrderProposal, m: StandingMandate
) -> None:
    signer = LocalSigner()
    g = await guardian_with(m, signer)
    if evaluate(p, m.model_copy(update={"signature": "x"}), {}).status == DecisionStatus.DECLINE:
        with pytest.raises(SigningRefused):
            await g.sign_order(SignOrderIn(proposal=p, consent=consent(p)))
        return
    out = await g.sign_order(SignOrderIn(proposal=p, consent=consent(p)))
    assert verify_signature(await signer.public_key(AGENT), canonical_json(p), b64d(out.vendor_sig))


@CFG
@given(p=proposals, m=mandates)
def test_counter_is_always_inside_the_mandate(p: OrderProposal, m: StandingMandate) -> None:
    d = evaluate(p, m, {})
    if d.status == DecisionStatus.RED:
        assert d.counter is not None and d.counter.version == p.version + 1
        assert evaluate(d.counter, m, {}).status in (DecisionStatus.GREEN, DecisionStatus.AMBER)


async def test_daily_max_is_enforced_across_orders() -> None:
    m = maya_mandate(
        auto_accept=maya_mandate().auto_accept.model_copy(update={"max_value": Decimal(10**6)})
    )
    g = await guardian_with(m)
    assert (await g.decide(DecideIn(proposal=proposal(200, "07:00", order_id="a")))).vendor_sig
    second = await g.decide(DecideIn(proposal=proposal(200, "07:00", order_id="b")))
    assert second.vendor_sig is None and second.status == DecisionStatus.RED
    assert second.counter is not None and second.counter.lines[0].qty == 100
    # Re-deciding the same order does not double count it.
    assert (await g.decide(DecideIn(proposal=proposal(200, "07:00", order_id="a")))).vendor_sig
    assert (await g.decide(DecideIn(proposal=proposal(100, "08:00", order_id="c")))).vendor_sig


async def test_mandate_needs_consent_and_own_signature() -> None:
    g = await guardian_with(maya_mandate())
    m = maya_mandate(signature=None).unsigned()
    with pytest.raises(SigningRefused):
        await g.sign_mandate(SignMandateIn(mandate=m, consent=consent({"other": "rules"})))
    forged = m.model_copy(update={"signature": "Zm9yZ2Vk", "payment_days_max": 60})
    with pytest.raises(SigningRefused):
        await g.install_mandate(forged)


async def test_without_signing_key_the_guardian_cannot_sign() -> None:
    registry = AgentRegistry(Settings(vl_config_dir=CONFIG_DIR))  # no signer given
    registry.register(AgentEntry(id="guardian", card="agents/core/guardian/agent.json"))
    g = cast(Guardian, registry.agent("guardian"))
    m = maya_mandate(signature=None).unsigned()
    with pytest.raises(SigningRefused, match="NO_SIGNING_KEY"):
        await g.sign_mandate(SignMandateIn(mandate=m, consent=consent(m)))
