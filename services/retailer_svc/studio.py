"""Configuration studio (M7): versioned configs on top of the files, and live activation."""

from __future__ import annotations

from typing import cast, get_args

from pydantic import BaseModel, ValidationError

from retailer_svc.store import ConfigVersion, RetailerStore
from vl_core.config import ConfigError, ConfigLoader
from vl_core.config.loader import KIND_MODELS
from vl_core.schemas import AgentCard, ConfigKind


class ConfigStudio:
    """A ConfigSource: studio versions first, then files. Writes always create a new version."""

    def __init__(self, loader: ConfigLoader, store: RetailerStore) -> None:
        self.loader = loader
        self.store = store
        self._cache: dict[tuple[str, str, int], BaseModel] = {}
        self._latest: dict[tuple[str, str], int] = {}

    async def refresh(self) -> None:
        self._cache.clear()
        self._latest.clear()
        for v in await self.store.configs.query():
            model = KIND_MODELS[cast(ConfigKind, v.kind)].model_validate(v.data)
            self._cache[(v.kind, v.config_id, v.version)] = model
            key = (v.kind, v.config_id)
            self._latest[key] = max(self._latest.get(key, 0), v.version)

    def load(self, kind: ConfigKind, config_id: str, version: int | None = None) -> BaseModel:
        latest = self._latest.get((kind, config_id))
        if latest is not None:
            wanted = latest if version is None else version
            cached = self._cache.get((kind, config_id, wanted))
            if cached is not None:
                return cached
        return self.loader.load(kind, config_id, version)

    def load_cards(self) -> list[AgentCard]:
        return self.loader.load_cards()

    def ids(self, kind: ConfigKind) -> list[str]:
        files = {p.stem for p in (self.loader.root / kind).glob("*.json") if "@" not in p.stem}
        return sorted(files | {cid for k, cid in self._latest if k == kind})

    async def save(self, kind: ConfigKind, config_id: str, data: dict[str, object]) -> BaseModel:
        current = self.load(kind, config_id)
        version = int(current.model_dump().get("version", 0)) + 1
        try:
            model = KIND_MODELS[kind].model_validate({**data, "version": version})
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc
        ident = model.model_dump(include={"id", "code"})
        if (ident.get("id") or ident.get("code")) != config_id:
            raise ConfigError("id in body must match the path")
        await self.store.configs.put(
            f"{kind}/{config_id}@{version}",
            ConfigVersion(
                kind=kind, config_id=config_id, version=version, data=model.model_dump(mode="json")
            ),
        )
        await self.refresh()
        return model


def config_kind(kind: str) -> ConfigKind:
    if kind not in get_args(ConfigKind):
        raise ConfigError(f"unknown config kind {kind!r}")
    return cast(ConfigKind, kind)
