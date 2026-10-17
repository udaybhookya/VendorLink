from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

from vl_core.settings import get_settings
from vl_core.store import reset_memory_repos

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
MODEL_VARS = ("FLASH_MODEL", "PRO_MODEL", "LIVE_MODEL")

# Tests are hermetic: models named in the developer's .env (or shell) stay off, so no network and
# no quota. Done at import, before test modules build their own Settings(); an empty environment
# variable wins over .env. Only the evals get the real models back: that is what they measure.
REAL_MODELS = {
    var: os.environ.get(var) or dotenv_values(ROOT / ".env").get(var) or "" for var in MODEL_VARS
}
for _var in MODEL_VARS:
    os.environ[_var] = ""


@pytest.fixture(autouse=True)
def _models_only_for_evals(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if ROOT / "tests" / "evals" in Path(request.node.path).parents:
        for var, value in REAL_MODELS.items():
            monkeypatch.setenv(var, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_memory_store() -> Iterator[None]:
    reset_memory_repos()
    yield
    reset_memory_repos()
