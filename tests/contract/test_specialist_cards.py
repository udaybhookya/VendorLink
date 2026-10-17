"""Agent contracts and architecture rules, checked on every agent in config/agents.json."""

from __future__ import annotations

import importlib
from pathlib import Path
from string import Template

import pytest

from agents.registry import AgentRegistry, agent_class, schema_model
from tests.conftest import CONFIG_DIR, ROOT
from vl_core.config import ConfigLoader
from vl_core.schemas import AgentCard

ENTRIES = ConfigLoader(CONFIG_DIR).load_agents_config().agents


def imports(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text().splitlines()
        if line.lstrip().startswith(("import ", "from "))
    ]


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.id)
def test_card_schemas_and_code_agree(entry) -> None:  # type: ignore[no-untyped-def]
    card = AgentCard.model_validate_json((ROOT / entry.card).read_text())
    assert card.agent_id == entry.id
    for spec in card.skills:
        schema_model(spec.input_schema)
        schema_model(spec.output_schema)
    cls = agent_class(card)
    assert cls.contract_problems(card) == []
    assert Path(entry.card).parent.name.replace("_", "-") == entry.id or card.kind == "core"


def test_kinds_live_in_their_folders() -> None:
    for entry in ENTRIES:
        card = AgentCard.model_validate_json((ROOT / entry.card).read_text())
        folder = {"core": "core", "specialist": "specialists", "channel": "channels"}[card.kind]
        assert entry.card.startswith(f"agents/{folder}/"), entry.card


def test_no_specialist_imports_another_agent() -> None:
    for path in (ROOT / "agents" / "specialists").rglob("*.py"):
        own = path.parent.name
        bad = [i for i in imports(path) if "agents.specialists." in i and own not in i]
        bad += [i for i in imports(path) if "agents.core." in i]
        assert not bad, (path, bad)


def test_nobody_imports_a_specialist() -> None:
    for path in [*(ROOT / "agents" / "core").rglob("*.py"), *(ROOT / "services").rglob("*.py")]:
        assert not [i for i in imports(path) if "agents.specialists" in i], path


def test_guardian_has_no_llm() -> None:
    for path in (ROOT / "agents" / "core" / "guardian").rglob("*.py"):
        assert not [i for i in imports(path) if "llm" in i or "genai" in i], path


def test_concierge_cannot_reach_a_signer_or_the_guardian() -> None:
    for path in (ROOT / "agents" / "core" / "concierge").rglob("*.py"):
        bad = [i for i in imports(path) if "guardian" in i or "crypto" in i or "store" in i]
        assert not bad, (path, bad)


def test_vendor_and_retailer_never_import_each_other() -> None:
    for svc, other in (("vendor_svc", "retailer_svc"), ("retailer_svc", "vendor_svc")):
        for path in (ROOT / "services" / svc).rglob("*.py"):
            assert not [i for i in imports(path) if other in i], path


def test_registry_validates() -> None:
    assert AgentRegistry.load().validate() == []


def prompt_constants(folder: Path) -> dict[str, object]:
    module = importlib.import_module(".".join(folder.relative_to(ROOT).parts) + ".prompts")
    return {k: v for k, v in vars(module).items() if k.isupper()}


def test_prompts_live_in_prompts_py_as_templates() -> None:
    assert not list((ROOT / "agents").rglob("prompt.md")), "prompts belong in prompts.py"
    for entry in ENTRIES:
        card = AgentCard.model_validate_json((ROOT / entry.card).read_text())
        folder = (ROOT / entry.card).parent
        if "llm" not in card.permissions or card.kind == "core":
            continue
        constants = prompt_constants(folder)
        assert constants, f"{entry.id} uses an LLM but has no prompts.py constants"
        for name, value in constants.items():
            assert isinstance(value, Template), f"{entry.id}.{name} must be a string.Template"


def test_every_prompt_placeholder_is_filled_by_its_agent() -> None:
    """A placeholder nobody fills would make .substitute() raise at call time; catch it here."""
    for folder in (ROOT / "agents" / "specialists").iterdir():
        if not (folder / "prompts.py").exists():
            continue
        source = (folder / "agent.py").read_text()
        for name, template in prompt_constants(folder).items():
            assert isinstance(template, Template)
            assert name in source, f"{folder.name}: prompt {name} is never used"
            for placeholder in template.get_identifiers():
                assert f"{placeholder}=" in source, f"{folder.name}: ${placeholder} never filled"


def test_vendor_text_with_dollar_signs_cannot_break_a_prompt() -> None:
    from agents.specialists.catalogue_builder.prompts import BUILD_LISTING

    text = BUILD_LISTING.substitute(
        language="en", categories="c", units="u", transcript="$5 a bunch, ${scale} $$"
    )
    assert "$5 a bunch, ${scale} $$" in text
