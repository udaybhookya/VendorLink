"""Deterministic read-backs in the vendor's language. The Concierge may reword, never replace."""

from __future__ import annotations

from decimal import Decimal
from zoneinfo import ZoneInfo

from vl_core.i18n import Strings
from vl_core.schemas import Decision, Listing, OrderProposal, StandingMandate
from vl_core.schemas.common import _decimal_str


def money(value: Decimal, currency: str) -> str:
    return f"{currency} {_decimal_str(value)}"


def deal_readback(p: OrderProposal, s: Strings, timezone: str, retailer_name: str) -> str:
    lines = ", ".join(
        s.t(
            "readback.line",
            qty=_decimal_str(line.qty),
            unit=line.unit,
            sku=line.sku,
            price=money(line.price, p.currency),
        )
        for line in p.lines
    )
    pickup = p.pickup_at.astimezone(ZoneInfo(timezone)).strftime("%d %b %H:%M")
    return s.t(
        "readback.deal",
        retailer=retailer_name,
        lines=lines,
        pickup=pickup,
        payment_days=p.payment_days,
        value=money(p.total_value(), p.currency),
    )


def party_name(party_id: str) -> str:
    """Readable name from a party id, e.g. retailer:freshcart → Freshcart."""
    return party_id.partition(":")[2].replace("-", " ").title() or party_id


def _sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!", "।")) else f"{text}."


def explain(decision: Decision, s: Strings) -> str:
    parts = [_sentence(s.t(f"decision.{decision.status.value}"))]
    for reason in decision.reasons:
        params = {
            k: _decimal_str(v) if isinstance(v, Decimal) else v for k, v in reason.params.items()
        }
        parts.append(s.t(f"reasons.{reason.code.value}", **params))
    return " ".join(parts)


def rules_readback(m: StandingMandate, s: Strings) -> str:
    skus = "; ".join(
        s.t(
            "readback.rule_sku",
            sku=r.sku,
            unit=r.unit,
            price_floor=money(r.price_floor, m.currency),
            max_qty=_decimal_str(r.max_qty_per_day),
        )
        for r in m.skus
    )
    windows = ", ".join(f"{w.start:%H:%M}–{w.end:%H:%M}" for w in m.pickup_windows)
    return s.t(
        "readback.rules",
        skus=skus,
        windows=windows,
        payment_days=m.payment_days_max,
        auto_accept=money(m.auto_accept.max_value, m.currency),
    )


def listing_readback(listing: Listing, s: Strings) -> str:
    return s.t(
        "readback.listing",
        sku_name=listing.sku_name,
        qty=_decimal_str(listing.qty),
        unit=listing.unit,
        price=money(listing.price, listing.currency),
        grade=listing.grade or "-",
    )
