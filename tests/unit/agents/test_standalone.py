"""Every agent runs on its own (standalone host) and the registry can reach it over A2A."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agents.host import create_agent_app
from agents.registry import AgentRegistry
from scripts._stack import AppRouter
from tests.conftest import CONFIG_DIR
from vl_core.a2a import set_transport
from vl_core.config import ConfigLoader, resolve_active_config
from vl_core.schemas import (
    AgentEntry,
    BuildListingIn,
    BuildListingOut,
    ListingStepIn,
    ListingStepOut,
)
from vl_core.settings import Settings

SETTINGS = Settings(vl_config_dir=CONFIG_DIR)
ALL = [e.id for e in ConfigLoader(CONFIG_DIR).load_agents_config().agents]


@pytest.mark.parametrize("agent_id", ALL)
def test_each_agent_runs_standalone_and_publishes_its_card(agent_id: str) -> None:
    client = TestClient(create_agent_app(agent_id, SETTINGS))
    assert client.get("/healthz").json()["agent"] == agent_id
    card = client.get("/.well-known/agent.json").json()
    assert card["agent_id"] == agent_id and card["transport"] == "a2a"
    assert all(not s["internal"] for s in card["skills"])


def test_internal_skills_are_not_exposed() -> None:
    client = TestClient(create_agent_app("guardian", SETTINGS))
    capabilities = {s["capability"] for s in client.get("/.well-known/agent.json").json()["skills"]}
    assert capabilities == {"guardian.evaluate"}
    r = client.post("/invoke/guardian.sign_order", json={})
    assert r.status_code == 404


def test_standalone_specialist_invoke_and_bad_input() -> None:
    a = resolve_active_config(
        "r", ConfigLoader(CONFIG_DIR).load_preset("B").refs, ConfigLoader(CONFIG_DIR)
    )
    client = TestClient(create_agent_app("label-checker", SETTINGS))
    step = ListingStepIn(
        vendor_id="v",
        language="id",
        label_text="Komposisi: x\nBerat bersih 1",
        business_profile=a.business_profile,
        retailer_profile=a.retailer_profile,
        country_pack=a.country_pack,
    )
    out = ListingStepOut.model_validate(
        client.post("/invoke/grade.label", json=step.model_dump(mode="json")).json()
    )
    assert "MISSING_REGISTRATION_PIRT" in {f.code for f in out.flags}
    assert client.post("/invoke/grade.label", json={"nope": 1}).status_code == 422


@pytest.fixture
def split_out_specialists() -> Iterator[AgentRegistry]:
    """The catalogue builder and produce grader run as their own services; the orchestrator
    reaches them over A2A purely because config says so."""
    urls = {"catalogue-builder": "http://catalogue.test", "produce-grader": "http://grader.test"}
    set_transport(AppRouter({url: create_agent_app(aid, SETTINGS) for aid, url in urls.items()}))
    registry = AgentRegistry(SETTINGS, host="retailer-svc")
    for entry in ConfigLoader(CONFIG_DIR).load_agents_config().agents:
        if entry.id in urls:
            entry = AgentEntry(
                id=entry.id, card=entry.card, transport="a2a", endpoint=urls[entry.id]
            )
        registry.register(entry)
    yield registry
    set_transport(None)


async def test_orchestrator_uses_split_out_specialists_over_a2a(
    split_out_specialists: AgentRegistry,
) -> None:
    registry = split_out_specialists
    routes = {r.card.agent_id: r.route for r in registry.registered()}
    assert routes["catalogue-builder"] == "a2a" and routes["orchestrator"] == "local"
    loader = ConfigLoader(CONFIG_DIR)
    a = resolve_active_config("r", loader.load_preset("A").refs, loader)
    step = ListingStepIn(
        vendor_id="v",
        language="en",
        transcript="20 bunch of mint at 12",
        business_profile=a.business_profile,
        retailer_profile=a.retailer_profile,
        country_pack=a.country_pack,
    )
    out = await registry.call(
        "orchestrator.build_listing", BuildListingIn(active_config=a, step_in=step), BuildListingOut
    )
    assert out.ran == ["catalogue-builder", "produce-grader"]
    assert out.result.listing is not None and out.result.listing.sku_name == "mint"
    assert "NO_PHOTO" in {f.code for f in out.result.flags}
