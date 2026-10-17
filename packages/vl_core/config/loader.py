"""Load configs from `config/<kind>/<id>.json` (or `<id>@<version>.json` for older versions)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ValidationError

from vl_core.crypto import hash_obj
from vl_core.schemas import (
    ActiveConfig,
    AgentCard,
    AgentEntry,
    AgentsConfig,
    BusinessProfile,
    ConfigKind,
    ConfigPreset,
    ConfigRef,
    ConfigRefs,
    CountryPack,
    RetailerPolicy,
    RetailerProfile,
    TrustScoring,
    WorkflowSet,
)
from vl_core.settings import get_settings

KIND_MODELS: dict[ConfigKind, type[BaseModel]] = {
    "business_profiles": BusinessProfile,
    "retailer_profiles": RetailerProfile,
    "country_packs": CountryPack,
    "retailer_policies": RetailerPolicy,
    "workflows": WorkflowSet,
}


class ConfigError(Exception):
    pass


def _ident(model: BaseModel) -> tuple[str, int]:
    """(id, version) of any config model; Country Packs use `code` as their id."""
    data = model.model_dump(include={"id", "code", "version"})
    return str(data.get("id") or data["code"]), int(data["version"])


class ConfigSource(Protocol):
    """Anything that can serve versioned configs and agent cards (files, or files + overlay)."""

    def load(self, kind: ConfigKind, config_id: str, version: int | None = None) -> BaseModel: ...

    def load_cards(self) -> list[AgentCard]: ...


class ConfigLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().vl_config_dir

    def _read(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"missing config file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON in {path}: {exc}") from exc

    def _validate[M: BaseModel](self, model: type[M], path: Path) -> M:
        try:
            return model.model_validate(self._read(path))
        except ValidationError as exc:
            raise ConfigError(f"{path}: {exc}") from exc

    def load(self, kind: ConfigKind, config_id: str, version: int | None = None) -> BaseModel:
        """Load one config. `version=None` means the current `<id>.json`."""
        model = KIND_MODELS[kind]
        current = self._validate(model, self.root / kind / f"{config_id}.json")
        current_id, current_version = _ident(current)
        if current_id != config_id:
            raise ConfigError(f"{kind}/{config_id}.json declares id {current_id!r}")
        if version is None or current_version == version:
            return current
        older = self._validate(model, self.root / kind / f"{config_id}@{version}.json")
        if _ident(older)[1] != version:
            raise ConfigError(f"{kind}/{config_id}@{version}.json declares another version")
        return older

    def load_preset(self, preset_id: str) -> ConfigPreset:
        return self._validate(ConfigPreset, self.root / "presets" / f"{preset_id}.json")

    @property
    def repo_root(self) -> Path:
        """Card paths in config/agents.json are relative to the repository root."""
        return self.root.resolve().parent

    def load_agents_config(self) -> AgentsConfig:
        return self._validate(AgentsConfig, self.root / "agents.json")

    def load_card(self, entry: AgentEntry) -> AgentCard:
        card = self._validate(AgentCard, self.repo_root / entry.card)
        if card.agent_id != entry.id:
            raise ConfigError(f"{entry.card} declares agent_id {card.agent_id!r}, not {entry.id!r}")
        return card

    def load_cards(self) -> list[AgentCard]:
        """Cards of every enabled agent in config/agents.json (data only; no agent code)."""
        return [self.load_card(e) for e in self.load_agents_config().agents if e.enabled]

    def trust_scoring(self) -> TrustScoring:
        """Trust-score weights from `scoring/trust.json`; the model defaults if it is absent."""
        path = self.root / "scoring" / "trust.json"
        return self._validate(TrustScoring, path) if path.exists() else TrustScoring()

    def validate_all(self) -> list[str]:
        """Validate every config file; return a list of problems (empty means OK)."""
        problems: list[str] = []
        for kind, model in KIND_MODELS.items():
            for path in sorted((self.root / kind).glob("*.json")):
                try:
                    self._validate(model, path)
                except ConfigError as exc:
                    problems.append(str(exc))
        for path in sorted((self.root / "presets").glob("*.json")):
            try:
                preset = self._validate(ConfigPreset, path)
                resolve_active_config("validate", preset.refs, self)
            except ConfigError as exc:
                problems.append(str(exc))
        try:
            self.trust_scoring()
        except ConfigError as exc:
            problems.append(str(exc))
        try:
            self.load_cards()
        except ConfigError as exc:
            problems.append(str(exc))
        return problems


def _load_ref(loader: ConfigSource, kind: ConfigKind, ref: ConfigRef) -> BaseModel:
    return loader.load(kind, ref.id, ref.version)


def resolve_active_config(
    retailer_id: str,
    refs: ConfigRefs,
    loader: ConfigSource | None = None,
    *,
    activated_at: datetime | None = None,
) -> ActiveConfig:
    """Load the four configs, pin their versions, resolve specialists and compute `config_hash`.

    The hash covers config content only, so the same refs always give the same hash.
    """
    loader = loader or ConfigLoader()
    bp = cast(BusinessProfile, _load_ref(loader, "business_profiles", refs.business_profile))
    rp = cast(RetailerProfile, _load_ref(loader, "retailer_profiles", refs.retailer_profile))
    cp = cast(CountryPack, _load_ref(loader, "country_packs", refs.country_pack))
    pol = cast(RetailerPolicy, _load_ref(loader, "retailer_policies", refs.retailer_policy))
    wf_ref = refs.workflows or ConfigRef(id=rp.workflow_set)
    wf = cast(WorkflowSet, _load_ref(loader, "workflows", wf_ref))

    specialists = list(dict.fromkeys([*rp.specialists, *bp.specialists]))
    known = {card.agent_id for card in loader.load_cards() if card.kind == "specialist"}
    missing = [agent_id for agent_id in specialists if agent_id not in known]
    if missing:
        raise ConfigError(f"not registered (or disabled) specialists in agents.json: {missing}")

    pinned = ConfigRefs(
        business_profile=ConfigRef(id=bp.id, version=bp.version),
        retailer_profile=ConfigRef(id=rp.id, version=rp.version),
        country_pack=ConfigRef(id=cp.code, version=cp.version),
        retailer_policy=ConfigRef(id=pol.id, version=pol.version),
        workflows=ConfigRef(id=wf.id, version=wf.version),
    )
    config_hash = hash_obj(
        {
            "business_profile": bp,
            "retailer_profile": rp,
            "country_pack": cp,
            "retailer_policy": pol,
            "workflows": wf,
            "specialists": specialists,
        }
    )
    return ActiveConfig(
        retailer_id=retailer_id,
        refs=pinned,
        business_profile=bp,
        retailer_profile=rp,
        country_pack=cp,
        retailer_policy=pol,
        workflows=wf,
        specialists=specialists,
        config_hash=config_hash,
        activated_at=activated_at or datetime.now(UTC),
    )
