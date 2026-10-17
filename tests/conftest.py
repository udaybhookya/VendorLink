from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

from vl_core.settings import get_settings
from vl_core.store import reset_memory_repos

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
MODEL_VARS = ("FLASH_MODEL", "PRO_MODEL", "LIVE_MODEL", "IMAGE_MODEL")

# Tests are hermetic: models named in the developer's .env (or shell) stay off, so no network and
# no quota. Done at import, before test modules build their own Settings(); an empty environment
# variable wins over .env. Only the evals get the real models back: that is what they measure.
_DOTENV = dotenv_values(ROOT / ".env")
REAL_MODELS = {  # a variable set in the shell wins, even when empty ("FLASH_MODEL= make evals")
    var: os.environ[var] if var in os.environ else (_DOTENV.get(var) or "") for var in MODEL_VARS
}
for _var in MODEL_VARS:
    os.environ[_var] = ""
# Likewise the store: a developer's .env may point at the sqlite store `make dev` is using, and
# tests must neither read that demo state nor wipe it (nor wait on its lock).
os.environ["VL_STORE"] = "memory"
os.environ["VL_CHECKPOINTS"] = "memory"
# And people: messages go to the local outbox, addressed to the contacts' defaults in config,
# never to the real Slack channels or inboxes a developer's .env names.
os.environ["VL_CHANNELS"] = "local"
for _path in sorted((CONFIG_DIR / "contacts").glob("*.json")):
    for _var, _default in re.findall(r"\$\{(\w+):-([^}]*)\}", _path.read_text()):
        os.environ[_var] = _default


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
