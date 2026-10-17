"""Demo steps over HTTP. Work against running services or the in-process stack alike."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from vl_core.a2a import make_client
from vl_core.settings import Settings

RETAILER = "retailer:freshcart"
MAYA = "vendor:maya"


class DemoError(AssertionError):
    pass


async def call(base: str, method: str, path: str, *, expect: int | None = 200, **kw: Any) -> Any:
    async with make_client(base, timeout=60) as client:
        response = await client.request(method, path, **kw)
    if expect is not None and response.status_code != expect:
        raise DemoError(f"{method} {path} → {response.status_code}: {response.text[:300]}")
    return response.json() if response.content else None


async def reset(s: Settings, preset: str = "A") -> None:
    await call(s.trust_gateway_url, "POST", "/dev/reset")
    await call(s.vendor_svc_url, "POST", "/dev/reset")
    await call(s.retailer_svc_url, "POST", "/dev/reset", params={"preset": preset})


async def set_language(s: Settings, language: str, vendor: str = MAYA) -> None:
    """Her preferred language, as the vendor app's language picker sets it (English by default)."""
    await call(s.vendor_svc_url, "PATCH", f"/vendors/{vendor}/profile", json={"language": language})


async def place(s: Settings, store: str, for_date: date, vendor: str = MAYA) -> dict[str, Any]:
    orders = await call(
        s.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/orders/place",
        json={"vendor_id": vendor, "store_id": store, "for_date": for_date.isoformat()},
    )
    if len(orders) != 1:
        raise DemoError(f"expected one order, got {orders}")
    return dict(orders[0])


async def retailer_order(s: Settings, order_id: str) -> dict[str, Any]:
    for order in await call(s.retailer_svc_url, "GET", f"/retailers/{RETAILER}/orders"):
        if order["order_id"] == order_id:
            return dict(order)
    raise DemoError(f"order {order_id} not found")


async def deal(s: Settings, order_id: str, vendor: str = MAYA) -> dict[str, Any]:
    for view in await call(s.vendor_svc_url, "GET", f"/vendors/{vendor}/deals"):
        if view["deal"]["order_id"] == order_id:
            return dict(view)
    raise DemoError(f"deal {order_id} not found")


async def say_yes(
    s: Settings,
    order_id: str,
    transcript: str = "అవును",
    language: str = "te",
    vendor: str = MAYA,
) -> dict[str, Any]:
    view = await deal(s, order_id, vendor)
    return dict(
        await call(
            s.vendor_svc_url,
            "POST",
            f"/vendors/{vendor}/deals/{order_id}/consent",
            json={
                "subject_hash": view["subject_hash"],
                "transcript": transcript,
                "language": language,
            },
        )
    )


def day(n: int) -> date:
    return date.today() + timedelta(days=n)


async def outbox(s: Settings, base: str | None = None) -> list[dict[str, Any]]:
    """Slack / Gmail messages sent while VL_CHANNELS=local."""
    return list(await call(base or s.retailer_svc_url, "GET", "/dev/outbox"))


async def reply(
    s: Settings, msg: dict[str, Any], text: str, by: str = "U-asha", base: str | None = None
) -> None:
    """Answer an outbox message as a person would in Slack or Gmail."""
    channel = msg["message"]["channel"]
    await call(
        base or s.retailer_svc_url,
        "POST",
        f"/dev/outbox/{channel}/{msg['id']}/reply",
        json={"text": text, "by": by},
    )


async def tick(s: Settings) -> dict[str, Any]:
    """One Liaison pass on both sides: read replies, remind, escalate."""
    await call(s.vendor_svc_url, "POST", "/dev/tick")
    return dict(await call(s.retailer_svc_url, "POST", "/dev/tick"))


async def use_terms(s: Settings, template_id: str, preset: str = "A") -> None:
    """The buyer puts a term template first in the active Retailer Profile (Studio, config API):
    new orders carry its payment days."""
    active = await call(s.retailer_svc_url, "GET", f"/retailers/{RETAILER}/config/active")
    profile = active["retailer_profile"]
    templates = sorted(profile["term_templates"], key=lambda t: t["id"] != template_id)
    if templates[0]["id"] != template_id:
        raise DemoError(f"no term template {template_id!r}")
    await call(
        s.retailer_svc_url,
        "PUT",
        f"/config/retailer_profiles/{profile['id']}",
        json={**profile, "term_templates": templates},
    )
    await call(
        s.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/config/activate",
        json={"preset": preset},
    )


# ---- demo scenes: state to start a demo from (scripts/demo_reset.py --scene …; docs/DEMO.md)

SARA = "vendor:sara"
SARA_LABEL = (
    "Spicy Cassava Chips\nKomposisi: singkong, minyak sawit, cabai, garam\n"
    "Berat bersih 100 g\nBaik digunakan sebelum 12/2026"
)


async def scene_waiting(s: Settings) -> dict[str, Any]:
    """Config A, with work waiting on people: tomorrow's order run has paused for the buyer
    (Slack, in the dev outbox) and asked Maya about rose candles (in-app, Telugu); FreshCart's
    payment terms wait on her Deals tab."""
    policy = await call(s.retailer_svc_url, "GET", "/config/retailer_policies/default_gifting")
    # 10 000: the gifting desk's 50 × ₹220 needs the buyer, the other stores go out on their own
    await call(
        s.retailer_svc_url,
        "PUT",
        "/config/retailer_policies/default_gifting",
        json={**policy, "auto_order_max_value": "10000"},
    )
    await call(
        s.retailer_svc_url, "POST", f"/retailers/{RETAILER}/config/activate", json={"preset": "A"}
    )
    run = await call(
        s.retailer_svc_url,
        "POST",
        f"/retailers/{RETAILER}/jobs/replenishment-run",
        json={"for_date": "tomorrow", "vendor_id": MAYA},
    )
    await call(s.retailer_svc_url, "POST", f"/retailers/{RETAILER}/terms/{MAYA}")
    return dict(run)


async def scene_sara(s: Settings) -> dict[str, Any]:
    """Config B: Sara has applied to FreshCart (approval waits in the queue) and her crisps
    listing, whose label has no P-IRT number, is flagged by the Label Checker."""
    await call(s.vendor_svc_url, "POST", f"/vendors/{SARA}/apply")
    return dict(
        await call(
            s.vendor_svc_url,
            "POST",
            f"/vendors/{SARA}/listings/draft",
            json={
                "vendor_id": SARA,
                "language": "id",
                "transcript": "spicy cassava chips 100 gram, 200 bungkus, 12000",
                "label_text": SARA_LABEL,
            },
        )
    )


SCENES = {"waiting": ("A", scene_waiting), "sara": ("B", scene_sara)}
