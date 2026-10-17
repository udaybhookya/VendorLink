from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from vl_core.schemas import (
    ActiveConfig,
    Listing,
    QueueItem,
    Retailer,
    RetailerOrder,
    RetailerVendor,
)
from vl_core.settings import Settings
from vl_core.store import Repo, get_repo

PREFIX = "retailer."


class ConfigVersion(BaseModel):
    """One config version saved from the studio (overlays the files in config/)."""

    kind: str
    config_id: str
    version: int
    data: dict[str, object]


@dataclass
class RetailerStore:
    retailers: Repo[Retailer]
    active: Repo[ActiveConfig]
    orders: Repo[RetailerOrder]
    queue: Repo[QueueItem]
    vendors: Repo[RetailerVendor]
    listings: Repo[Listing]
    configs: Repo[ConfigVersion]

    @classmethod
    def create(cls, settings: Settings) -> RetailerStore:
        def repo[T: BaseModel](model: type[T], name: str) -> Repo[T]:
            return get_repo(model, f"{PREFIX}{name}", settings)

        return cls(
            retailers=repo(Retailer, "retailers"),
            active=repo(ActiveConfig, "active_config"),
            orders=repo(RetailerOrder, "orders"),
            queue=repo(QueueItem, "queue"),
            vendors=repo(RetailerVendor, "vendors"),
            listings=repo(Listing, "listings"),
            configs=repo(ConfigVersion, "config_versions"),
        )
