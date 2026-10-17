from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from vl_core.store import LocalBlobStore, MemoryRepo, get_repo


class Item(BaseModel):
    id: str
    kind: str
    tags: list[str] = []


async def test_put_get_query() -> None:
    repo = MemoryRepo(Item, "items")
    await repo.put("1", Item(id="1", kind="a"))
    await repo.put("2", Item(id="2", kind="b"))
    assert (await repo.get("1")) == Item(id="1", kind="a")
    assert await repo.get("missing") is None
    assert [i.id for i in await repo.query(kind="b")] == ["2"]
    assert len(await repo.query()) == 2


async def test_stored_objects_are_copies() -> None:
    repo = MemoryRepo(Item, "items")
    item = Item(id="1", kind="a")
    await repo.put("1", item)
    item.tags.append("mutated")
    assert (await repo.get("1")).tags == []  # type: ignore[union-attr]


async def test_watch_yields_new_writes() -> None:
    repo = MemoryRepo(Item, "items")
    stream = repo.watch()
    next_item = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await repo.put("1", Item(id="1", kind="a"))
    assert (await asyncio.wait_for(next_item, 1)).id == "1"
    await stream.aclose()


def test_get_repo_shares_collection() -> None:
    assert get_repo(Item, "items") is get_repo(Item, "items")

    class Other(BaseModel):
        id: str

    with pytest.raises(TypeError):
        get_repo(Other, "items")


async def test_local_blob_roundtrip_and_traversal(tmp_path: Path) -> None:
    blobs = LocalBlobStore(tmp_path)
    uri = await blobs.put("photos/a.jpg", b"img", "image/jpeg")
    assert await blobs.get(uri) == b"img"
    for bad in ("../escape", "/abs/path", ""):
        with pytest.raises(ValueError):
            await blobs.put(bad, b"x", "text/plain")


# ---- SQLite: survives a restart --------------------------------------------------------------


async def test_sqlite_persists_across_reopen(tmp_path: Path) -> None:
    from vl_core.settings import Settings
    from vl_core.store import get_repo, reset_memory_repos, reset_repos

    settings = Settings(vl_store="sqlite", vl_data_dir=tmp_path)
    repo = get_repo(Item, "vendor.items", settings)
    await repo.put("1", Item(id="1", kind="a", tags=["x"]))
    await repo.put("2", Item(id="2", kind="b"))
    assert [i.id for i in await repo.query(kind="b")] == ["2"]

    reset_memory_repos()  # as if the process restarted
    reopened = get_repo(Item, "vendor.items", settings)
    assert reopened is not repo
    assert (await reopened.get("1")) == Item(id="1", kind="a", tags=["x"])
    assert "store-vendor.sqlite" in {p.name for p in tmp_path.iterdir()}

    await reset_repos("vendor.")
    assert await reopened.query() == []


async def test_sqlite_namespaces_use_separate_files(tmp_path: Path) -> None:
    from vl_core.settings import Settings
    from vl_core.store import get_repo

    settings = Settings(vl_store="sqlite", vl_data_dir=tmp_path)
    await get_repo(Item, "gw.things", settings).put("1", Item(id="1", kind="a"))
    await get_repo(Item, "retailer.things", settings).put("1", Item(id="1", kind="b"))
    assert {p.name for p in tmp_path.glob("store-*.sqlite")} == {
        "store-gw.sqlite",
        "store-retailer.sqlite",
    }


# ---- Firestore adapter, against a fake async client ---------------------------------------------


class _Snap:
    def __init__(self, data: dict | None) -> None:  # type: ignore[type-arg]
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        return dict(self._data or {})


class _Doc:
    def __init__(self, store: dict, key: str) -> None:  # type: ignore[type-arg]
        self.store, self.key = store, key

    async def get(self) -> _Snap:
        return _Snap(self.store.get(self.key))

    async def set(self, data: dict) -> None:  # type: ignore[type-arg]
        self.store[self.key] = data


class _Query:
    def __init__(self, store: dict, filters: list | None = None) -> None:  # type: ignore[type-arg]
        self.store, self.filters = store, filters or []

    def document(self, key: str) -> _Doc:
        return _Doc(self.store, key)

    def where(self, *, filter: object) -> _Query:
        return _Query(self.store, [*self.filters, filter])

    async def stream(self):  # type: ignore[no-untyped-def]
        for data in self.store.values():
            if all(data.get(f.field_path) == f.value for f in self.filters):  # type: ignore[attr-defined]
                yield _Snap(data)


class _Client:
    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}  # type: ignore[type-arg]

    def collection(self, name: str) -> _Query:
        return _Query(self.collections.setdefault(name, {}))


class _FieldFilter:
    def __init__(self, field_path: str, op: str, value: object) -> None:
        self.field_path, self.op, self.value = field_path, op, value


async def test_firestore_repo_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types
    from datetime import date

    from vl_core.settings import Settings
    from vl_core.store import FirestoreRepo

    module = types.ModuleType("google.cloud.firestore_v1.base_query")
    module.FieldFilter = _FieldFilter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.cloud.firestore_v1.base_query", module)

    class Dated(BaseModel):
        id: str
        day: date

    client = _Client()
    repo = FirestoreRepo(Dated, "vendor.dated", Settings(), client=client)
    await repo.put("retailer_policies/default_qc@2", Dated(id="a", day=date(2026, 10, 15)))
    assert "retailer_policies%2Fdefault_qc@2" in client.collections["vendor.dated"]
    assert (await repo.get("retailer_policies/default_qc@2")) == Dated(
        id="a", day=date(2026, 10, 15)
    )
    assert [d.id for d in await repo.query(day=date(2026, 10, 15))] == ["a"]
    assert await repo.query(day=date(2026, 10, 16)) == []
    assert await repo.get("missing") is None
