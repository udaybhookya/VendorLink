"""Pure decision rules. Deterministic: same proposal, mandate and commitments → same Decision.

Usable on their own (`evaluate`) and by the Guardian agent. No LLM, no I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from vl_core.schemas import (
    Decision,
    DecisionStatus,
    OrderLine,
    OrderProposal,
    Reason,
    ReasonCode,
    StandingMandate,
)

DECLINE_CODES = {
    ReasonCode.BUYER_NOT_APPROVED,
    ReasonCode.SKU_NOT_IN_MANDATE,
    ReasonCode.CURRENCY_MISMATCH,
    ReasonCode.DAILY_MAX_REACHED,
}


def local_pickup(proposal: OrderProposal, mandate: StandingMandate) -> datetime:
    return proposal.pickup_at.astimezone(ZoneInfo(mandate.timezone))


def pickup_date(proposal: OrderProposal, mandate: StandingMandate) -> date:
    return local_pickup(proposal, mandate).date()


def _fmt(t: time) -> str:
    return t.strftime("%H:%M")


def _allowed_pickup(
    local: datetime, mandate: StandingMandate
) -> tuple[ReasonCode, datetime] | None:
    """None if `local` is inside a window; else the reason and the earliest allowed time after."""
    t = local.time().replace(tzinfo=None)
    windows = sorted(mandate.pickup_windows, key=lambda w: w.start)
    if any(w.start <= t <= w.end for w in windows):
        return None
    later = [w for w in windows if w.start > t]
    if later:
        return ReasonCode.PICKUP_BEFORE_WINDOW, local.replace(
            hour=later[0].start.hour, minute=later[0].start.minute, second=0, microsecond=0
        )
    first = windows[0].start
    next_day = (local + timedelta(days=1)).replace(
        hour=first.hour, minute=first.minute, second=0, microsecond=0
    )
    return ReasonCode.PICKUP_AFTER_WINDOW, next_day


def evaluate(
    proposal: OrderProposal,
    mandate: StandingMandate | None,
    committed: Mapping[str, Decimal],
) -> Decision:
    """Decide green / amber / red (with counter) / decline.

    `committed` is quantity per sku already signed for the proposal's local pickup date,
    excluding this order.
    """

    def decision(
        status: DecisionStatus, reasons: list[Reason], counter: OrderProposal | None = None
    ) -> Decision:
        return Decision(
            order_id=proposal.order_id,
            version=proposal.version,
            status=status,
            reasons=reasons,
            counter=counter,
        )

    if mandate is None or mandate.signature is None:
        return decision(DecisionStatus.AMBER, [Reason(code=ReasonCode.NO_STANDING_MANDATE)])

    reasons: list[Reason] = []
    if proposal.retailer_id not in mandate.approved_buyers:
        reasons.append(
            Reason(code=ReasonCode.BUYER_NOT_APPROVED, params={"buyer": proposal.retailer_id})
        )
    if proposal.currency != mandate.currency:
        reasons.append(
            Reason(
                code=ReasonCode.CURRENCY_MISMATCH,
                params={"offered": proposal.currency, "expected": mandate.currency},
            )
        )

    requested: dict[str, Decimal] = defaultdict(Decimal)
    for line in proposal.lines:
        rule = mandate.rule_for(line.sku)
        if rule is None or rule.unit != line.unit:
            reasons.append(
                Reason(
                    code=ReasonCode.SKU_NOT_IN_MANDATE, params={"sku": line.sku, "unit": line.unit}
                )
            )
            continue
        requested[line.sku] += line.qty
        if line.price < rule.price_floor:
            reasons.append(
                Reason(
                    code=ReasonCode.PRICE_BELOW_FLOOR,
                    params={"sku": line.sku, "offered": line.price, "floor": rule.price_floor},
                )
            )

    allowed: dict[str, Decimal] = {}
    for sku, qty in requested.items():
        rule = mandate.rule_for(sku)
        assert rule is not None
        allowed[sku] = rule.max_qty_per_day - committed.get(sku, Decimal(0))
        if allowed[sku] <= 0:
            reasons.append(Reason(code=ReasonCode.DAILY_MAX_REACHED, params={"sku": sku}))
        elif qty > allowed[sku]:
            reasons.append(
                Reason(
                    code=ReasonCode.QTY_OVER_DAILY_MAX,
                    params={"sku": sku, "requested": qty, "allowed": allowed[sku]},
                )
            )

    local = local_pickup(proposal, mandate)
    pickup_fix = _allowed_pickup(local, mandate)
    if pickup_fix is not None:
        code, earliest = pickup_fix
        reasons.append(
            Reason(
                code=code,
                params={"requested": _fmt(local.time()), "earliest": _fmt(earliest.time())},
            )
        )

    if proposal.payment_days > mandate.payment_days_max:
        reasons.append(
            Reason(
                code=ReasonCode.PAYMENT_TOO_LATE,
                params={
                    "requested_days": proposal.payment_days,
                    "max_days": mandate.payment_days_max,
                },
            )
        )

    if any(r.code in DECLINE_CODES for r in reasons):
        return decision(DecisionStatus.DECLINE, reasons)
    if reasons:
        counter = _counter(proposal, mandate, allowed, pickup_fix)
        return decision(DecisionStatus.RED, reasons, counter)

    value = proposal.total_value()
    if value > mandate.auto_accept.max_value:
        return decision(
            DecisionStatus.AMBER,
            [
                Reason(
                    code=ReasonCode.VALUE_OVER_AUTO_ACCEPT,
                    params={"value": value, "max_value": mandate.auto_accept.max_value},
                )
            ],
        )
    return decision(DecisionStatus.GREEN, [])


def _counter(
    proposal: OrderProposal,
    mandate: StandingMandate,
    allowed: Mapping[str, Decimal],
    pickup_fix: tuple[ReasonCode, datetime] | None,
) -> OrderProposal:
    """Closest proposal inside the mandate: clamp qty, raise price, move pickup, cap payment."""
    budget = dict(allowed)
    lines: list[OrderLine] = []
    for line in proposal.lines:
        rule = mandate.rule_for(line.sku)
        assert rule is not None
        qty = min(line.qty, budget[line.sku])
        budget[line.sku] -= qty
        if qty > 0:
            lines.append(
                OrderLine(
                    sku=line.sku, qty=qty, unit=line.unit, price=max(line.price, rule.price_floor)
                )
            )
    pickup_at = proposal.pickup_at if pickup_fix is None else pickup_fix[1]
    return proposal.next_version(
        lines=tuple(lines),
        pickup_at=pickup_at,
        payment_days=min(proposal.payment_days, mandate.payment_days_max),
    )


def within_mandate(
    proposal: OrderProposal, mandate: StandingMandate, committed: Mapping[str, Decimal]
) -> bool:
    """True when every rule holds (ignores the auto-accept value limit)."""
    return evaluate(proposal, mandate, committed).status in (
        DecisionStatus.GREEN,
        DecisionStatus.AMBER,
    )
