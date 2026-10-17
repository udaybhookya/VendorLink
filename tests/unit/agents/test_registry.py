"""The agent registry: config-driven, capability lookup, tenancy, local and A2A routes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from agents.registry import AgentRegistry, RegistryError
from tests.conftest import CONFIG_DIR, ROOT
from vl_core.config import ConfigLoader, resolve_active_config
from vl_core.registry import capability_matches
from vl_core.schemas import ActiveConfig, AgentCard, AgentEntry, ListingStepIn, ListingStepOut
from vl_core.settings import Settings

SETTINGS = Settings(vl_config_dir=CONFIG_DIR)


def active(preset: str) -> ActiveConfig:
    loader = ConfigLoader(CONFIG_DIR)
    return resolve_active_config("retailer:demo", loader.load_preset(preset).refs, loader)


def test_capability_wildcard() -> None:
    assert capability_matches("grade.*", "grade.produce")
    assert not capability_matches("grade.*", "gradex.produce")
    assert not capability_matches("order", "order.create")


def test_repository_config_is_valid() -> None:
    assert AgentRegistry.load(SETTINGS).validate() == []


def test_every_agent_is_registered_with_a_card_next_to_its_code() -> None:
    config = ConfigLoader(CONFIG_DIR).load_agents_config()
    folders = {p.parent for p in (ROOT / "agents").glob("*/*/agent.json")}
    assert {ROOT / Path(e.card).parent for e in config.agents} == folders


def test_grade_wildcard_follows_active_config() -> None:
    registry = AgentRegistry.load(SETTINGS, host="retailer-svc")
    assert registry.find("grade.*", active("A")).agent_id == "craft-quality-checker"
    assert registry.find("grade.*", active("B")).agent_id == "label-checker"
    with pytest.raises(RegistryError):
        registry.find("compliance.check", active("A"))


def test_hosts_decide_where_agents_run() -> None:
    vendor = AgentRegistry.load(SETTINGS, host="vendor-svc")
    retailer = AgentRegistry.load(SETTINGS, host="retailer-svc")
    routes = {r.card.agent_id: r.route for r in vendor.registered()}
    assert routes["guardian"] == "local" and routes["concierge"] == "local"
    assert routes["orchestrator"] is None and routes["produce-grader"] is None
    routes = {r.card.agent_id: r.route for r in retailer.registered()}
    assert routes["orchestrator"] == "local" and routes["guardian"] is None
    with pytest.raises(RegistryError, match="not available"):
        # The retailer tenant can never reach the Guardian in-process.
        import asyncio

        asyncio.run(retailer.invoke("guardian", "guardian.mandate", {"vendor_id": "v"}))


def test_vendor_agent_cannot_be_hosted_by_retailer() -> None:
    registry = AgentRegistry(SETTINGS, host="retailer-svc")
    with pytest.raises(RegistryError, match="vendor-tenant agent"):
        registry.register(
            AgentEntry(
                id="guardian", card="agents/core/guardian/agent.json", hosts=["retailer-svc"]
            )
        )


def test_only_the_guardian_gets_a_signer() -> None:
    from vl_core.crypto import LocalSigner

    registry = AgentRegistry.load(SETTINGS, host="vendor-svc", signer=LocalSigner())
    assert registry.agent("guardian").ctx.signer is not None
    assert registry.agent("concierge").ctx.signer is None
    signers = [c.agent_id for c in registry.cards if "sign" in c.permissions]
    assert signers == ["guardian"]


def test_bad_settings_are_rejected() -> None:
    registry = AgentRegistry(SETTINGS)
    registry.register(
        AgentEntry(
            id="produce-grader",
            card="agents/specialists/produce_grader/agent.json",
            settings={"llm_tier": "huge"},
        )
    )
    with pytest.raises(RegistryError, match="bad settings"):
        registry.agent("produce-grader")


@pytest.fixture
def step_in() -> ListingStepIn:
    a = active("A")
    return ListingStepIn(
        vendor_id="vendor:x",
        language="en",
        transcript="20 bunch of mint at 12",
        business_profile=a.business_profile,
        retailer_profile=a.retailer_profile,
        country_pack=a.country_pack,
    )


async def test_local_invoke_validates_input_and_output(step_in: ListingStepIn) -> None:
    registry = AgentRegistry.load(SETTINGS)
    out = await registry.invoke("catalogue-builder", "catalogue.build", step_in)
    assert isinstance(out, ListingStepOut) and out.listing is not None
    from agents.base import AgentError

    with pytest.raises(AgentError, match="bad input"):
        await registry.invoke("catalogue-builder", "catalogue.build", {"wrong": 1})


@pytest.fixture
def external_card(tmp_path: Path) -> Iterator[AgentCard]:
    yield AgentCard.model_validate(
        {
            "agent_id": "pricing-advisor",
            "name": "Pricing Advisor (external)",
            "kind": "specialist",
            "tenant": "retailer",
            "transport": "a2a",
            "endpoint": "${PRICING_URL:-http://pricing.test}",
            "skills": [
                {
                    "capability": "grade.price",
                    "input_schema": "ListingStepIn",
                    "output_schema": "ListingStepOut",
                }
            ],
        }
    )


def test_external_agent_added_by_config_only(external_card: AgentCard, tmp_path: Path) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(external_card.model_dump_json())
    registry = AgentRegistry(SETTINGS, host="retailer-svc")
    registered = registry.register(AgentEntry(id="pricing-advisor", card=str(path)))
    assert registered.route == "a2a" and registered.card.endpoint == "http://pricing.test"
    assert registry.validate() == []
    assert json.loads(path.read_text())["agent_id"] == "pricing-advisor"
