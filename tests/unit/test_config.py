import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import CONFIG_DIR
from vl_core.config import ConfigError, ConfigLoader, resolve_active_config


@pytest.fixture
def loader() -> ConfigLoader:
    return ConfigLoader(CONFIG_DIR)


def test_all_config_files_validate(loader: ConfigLoader) -> None:
    assert loader.validate_all() == []


def test_configs_a_and_b_differ(loader: ConfigLoader) -> None:
    a = resolve_active_config("retailer:demo", loader.load_preset("A").refs, loader)
    b = resolve_active_config("retailer:demo", loader.load_preset("B").refs, loader)
    assert "produce-grader" in a.specialists and "replenishment-planner" in a.specialists
    assert "label-checker" in b.specialists and "compliance-auditor" in b.specialists
    assert "produce-grader" not in b.specialists and "label-checker" not in a.specialists
    assert a.config_hash != b.config_hash
    assert (a.country_pack.currency, b.country_pack.currency) == ("INR", "IDR")


def test_config_hash_is_stable(loader: ConfigLoader) -> None:
    refs = loader.load_preset("A").refs
    first = resolve_active_config("retailer:one", refs, loader)
    second = resolve_active_config("retailer:two", refs, loader)
    assert first.config_hash == second.config_hash
    assert first.refs.business_profile.version == 1


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    root = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, root)
    (tmp_path / "agents").symlink_to(CONFIG_DIR.parent / "agents")
    return root


def test_older_version_loads_and_changes_hash(tmp_config: Path) -> None:
    current = tmp_config / "retailer_policies" / "default_qc.json"
    data = json.loads(current.read_text())
    (tmp_config / "retailer_policies" / "default_qc@1.json").write_text(json.dumps(data))
    current.write_text(json.dumps({**data, "version": 2, "auto_accept_counter_max_delta": 0.05}))

    loader = ConfigLoader(tmp_config)
    refs = loader.load_preset("A").refs
    latest = resolve_active_config("r", refs, loader)
    pinned_refs = refs.model_copy(
        update={"retailer_policy": refs.retailer_policy.model_copy(update={"version": 1})}
    )
    pinned = resolve_active_config("r", pinned_refs, loader)
    assert latest.retailer_policy.version == 2 and pinned.retailer_policy.version == 1
    assert latest.config_hash != pinned.config_hash


def test_disabled_specialist_is_an_error(tmp_config: Path) -> None:
    path = tmp_config / "agents.json"
    data = json.loads(path.read_text())
    for entry in data["agents"]:
        if entry["id"] == "produce-grader":
            entry["enabled"] = False
    path.write_text(json.dumps(data))
    loader = ConfigLoader(tmp_config)
    with pytest.raises(ConfigError, match="produce-grader"):
        resolve_active_config("r", loader.load_preset("A").refs, loader)


def test_id_must_match_filename(tmp_config: Path) -> None:
    path = tmp_config / "country_packs" / "IN.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "code": "ID"}))
    with pytest.raises(ConfigError, match="declares id"):
        ConfigLoader(tmp_config).load("country_packs", "IN")


def test_dotenv_reaches_the_process_environment_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from vl_core.settings import export_dotenv

    env = tmp_path / ".env"
    env.write_text("VL_TEST_FROM_DOTENV=from-file\nVL_TEST_ALREADY_SET=from-file\n")
    monkeypatch.delenv("VL_TEST_FROM_DOTENV", raising=False)
    monkeypatch.setenv("VL_TEST_ALREADY_SET", "from-shell")
    export_dotenv(env)
    assert os.environ["VL_TEST_FROM_DOTENV"] == "from-file"
    assert os.environ["VL_TEST_ALREADY_SET"] == "from-shell"
    monkeypatch.delenv("VL_TEST_FROM_DOTENV")
