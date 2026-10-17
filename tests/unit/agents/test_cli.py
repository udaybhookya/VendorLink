"""`python -m agents new` scaffolds an agent that already satisfies its own contract."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from agents.__main__ import main
from tests.conftest import CONFIG_DIR
from vl_core.schemas import AgentCard, AgentsConfig
from vl_core.settings import get_settings


def test_new_scaffolds_and_registers_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VL_CONFIG_DIR", str(tmp_path / "config"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agents",
            "new",
            "pricing-advisor",
            "--capability",
            "price.advise",
            "--input",
            "ListingStepIn",
            "--output",
            "ListingStepOut",
            "--description",
            "Suggest a price",
            "--llm",
        ],
    )
    try:
        assert main() == 0
    finally:
        get_settings.cache_clear()
    folder = tmp_path / "agents" / "specialists" / "pricing_advisor"
    card = AgentCard.model_validate_json((folder / "agent.json").read_text())
    assert card.entrypoint == "agents.specialists.pricing_advisor.agent:PricingAdvisor"
    spec = importlib.util.spec_from_file_location("scaffolded", folder / "agent.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PricingAdvisor.contract_problems(card) == []
    assert "llm" in card.permissions
    prompts = (folder / "prompts.py").read_text()
    namespace: dict[str, object] = {}
    exec(compile(prompts, "prompts.py", "exec"), namespace)
    from string import Template

    assert isinstance(namespace["MAIN"], Template)
    assert "$placeholders" in namespace["MAIN"].substitute()
    config = AgentsConfig.model_validate(json.loads((tmp_path / "config/agents.json").read_text()))
    entry = next(e for e in config.agents if e.id == "pricing-advisor")
    assert not entry.enabled and entry.hosts == ["retailer-svc"]
