"""Document storage behind one interface.

    VL_STORE=memory     tests (per process, lost on restart)
    VL_STORE=sqlite     local dev: survives restarts; one file per service namespace
                        (gw / vendor / retailer) under VL_DATA_DIR, so processes never contend
    VL_STORE=firestore  deploy (Cloud Firestore)

Collections are namespaced by the first dotted part ("vendor.deals"). Equality queries on
top-level fields only. `watch` yields writes made through this process (all three backends).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel

from vl_core.settings import Settings, get_settings


class Repo[T: BaseModel](Protocol):
    async def get(self, doc_id: str) -> T | None: ...

    async def put(self, doc_id: str, obj: T) -> None: ...

    async def query(self, **equals: Any) -> list[T]:
        """Documents whose top-level fields equal every keyword given (all if none)."""
        ...

    def watch(self) -> AsyncIterator[T]:
        """Yield each document as it is written from now on."""
        ...


def _jsonable(value: Any) -> Any:
    """A query value in the form documents are stored in (model_dump(mode="json"))."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class _Fanout[T: BaseModel]:
    """In-process watchers shared by every backend."""

    def __init__(self) -> None:
        self._watchers: set[asyncio.Queue[T]] = set()

    def notify(self, obj: T) -> None:
        for queue in self._watchers:
            queue.put_nowait(obj)

    async def watch(self) -> AsyncIterator[T]:
        queue: asyncio.Queue[T] = asyncio.Queue()
        self._watchers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._watchers.discard(queue)


class MemoryRepo[T: BaseModel]:
    """In-process store. Objects are stored as JSON dumps so callers never share instances."""

    def __init__(self, model: type[T], collection: str) -> None:
        self.model = model
        self.collection = collection
        self._docs: dict[str, dict[str, Any]] = {}
        self._fanout: _Fanout[T] = _Fanout()

    async def clear(self) -> None:
        self._docs.clear()

    async def get(self, doc_id: str) -> T | None:
        raw = self._docs.get(doc_id)
        return None if raw is None else self.model.model_validate(raw)

    async def put(self, doc_id: str, obj: T) -> None:
        self._docs[doc_id] = obj.model_dump(mode="json")
        self._fanout.notify(self.model.model_validate(self._docs[doc_id]))

    async def query(self, **equals: Any) -> list[T]:
        items = [self.model.model_validate(raw) for raw in self._docs.values()]
        return [item for item in items if all(getattr(item, k) == v for k, v in equals.items())]

    def watch(self) -> AsyncIterator[T]:
        return self._fanout.watch()


class _SqliteFile:
    """One aiosqlite connection per database file, opened lazily on the running loop."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: Any = None
        self._lock = asyncio.Lock()

    async def conn(self) -> Any:
        if self._conn is None:
            import aiosqlite

            await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.path)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


class SqliteRepo[T: BaseModel]:
    """Local persistent store: a table per collection, JSON documents."""

    def __init__(self, model: type[T], collection: str, db: _SqliteFile) -> None:
        self.model = model
        self.collection = collection
        self.table = "c_" + re.sub(r"[^A-Za-z0-9_]", "_", collection)
        self._db = db
        self._ready = False
        self._fanout: _Fanout[T] = _Fanout()

    async def _conn(self) -> Any:
        conn = await self._db.conn()
        if not self._ready:
            ddl = f'CREATE TABLE IF NOT EXISTS "{self.table}" (id TEXT PRIMARY KEY, data TEXT)'
            await conn.execute(ddl)
            await conn.commit()
            self._ready = True
        return conn

    async def get(self, doc_id: str) -> T | None:
        conn = await self._conn()
        async with conn.execute(f'SELECT data FROM "{self.table}" WHERE id = ?', (doc_id,)) as cur:
            row = await cur.fetchone()
        return None if row is None else self.model.model_validate_json(row[0])

    async def put(self, doc_id: str, obj: T) -> None:
        conn = await self._conn()
        data = obj.model_dump_json()
        async with self._db.lock:
            await conn.execute(
                f'INSERT INTO "{self.table}" (id, data) VALUES (?, ?) '
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (doc_id, data),
            )
            await conn.commit()
        self._fanout.notify(self.model.model_validate_json(data))

    async def query(self, **equals: Any) -> list[T]:
        conn = await self._conn()
        async with conn.execute(f'SELECT data FROM "{self.table}"') as cur:
            rows = await cur.fetchall()
        items = [self.model.model_validate_json(r[0]) for r in rows]
        return [item for item in items if all(getattr(item, k) == v for k, v in equals.items())]

    async def clear(self) -> None:
        conn = await self._conn()
        async with self._db.lock:
            await conn.execute(f'DELETE FROM "{self.table}"')
            await conn.commit()

    def watch(self) -> AsyncIterator[T]:
        return self._fanout.watch()


_firestore_client: Any = None


def _firestore(settings: Settings) -> Any:
    global _firestore_client
    if _firestore_client is None:
        from google.cloud import firestore

        _firestore_client = firestore.AsyncClient(project=settings.google_cloud_project or None)
    return _firestore_client


def _firestore_id(doc_id: str) -> str:
    """Firestore treats '/' as a path separator; keep ids flat and reversible."""
    return doc_id.replace("%", "%25").replace("/", "%2F")


_CLEAR_BATCH = 400  # Firestore allows at most 500 writes per batch


class FirestoreRepo[T: BaseModel]:
    """Cloud Firestore. Documents are the model's JSON form; equality filters run server-side."""

    def __init__(self, model: type[T], collection: str, settings: Settings, client: Any = None):
        self.model = model
        self.collection = collection
        self._client = client if client is not None else _firestore(settings)
        self._fanout: _Fanout[T] = _Fanout()
        self._may_clear = settings.vl_demo_endpoints

    def _col(self) -> Any:
        return self._client.collection(self.collection)

    async def get(self, doc_id: str) -> T | None:
        snapshot = await self._col().document(_firestore_id(doc_id)).get()
        return self.model.model_validate(snapshot.to_dict()) if snapshot.exists else None

    async def put(self, doc_id: str, obj: T) -> None:
        data = obj.model_dump(mode="json")
        await self._col().document(_firestore_id(doc_id)).set(data)
        self._fanout.notify(self.model.model_validate(data))

    async def query(self, **equals: Any) -> list[T]:
        query = self._col()
        if equals:
            from google.cloud.firestore_v1.base_query import FieldFilter

            for key, value in equals.items():
                query = query.where(filter=FieldFilter(key, "==", _jsonable(value)))
        return [self.model.model_validate(snap.to_dict()) async for snap in query.stream()]

    async def clear(self) -> None:
        """Demo reset only: a hosted demo of synthetic data (VL_DEMO_ENDPOINTS, ADR 0014)."""
        if not self._may_clear:
            raise RuntimeError("refusing to bulk-delete a Firestore collection from a dev reset")
        while True:
            snaps = [snap async for snap in self._col().limit(_CLEAR_BATCH).stream()]
            if not snaps:
                return
            batch = self._client.batch()
            for snap in snaps:
                batch.delete(snap.reference)
            await batch.commit()

    def watch(self) -> AsyncIterator[T]:
        return self._fanout.watch()


_repos: dict[str, Any] = {}
_sqlite_files: dict[Path, _SqliteFile] = {}


def get_repo[T: BaseModel](
    model: type[T], collection: str, settings: Settings | None = None
) -> Repo[T]:
    """Process-wide repo for `collection`; the same collection always returns the same repo."""
    settings = settings or get_settings()
    repo = _repos.get(collection)
    if repo is not None:
        if repo.model is not model:
            raise TypeError(f"collection {collection!r} already holds {repo.model.__name__}")
        return cast(Repo[T], repo)
    if settings.vl_store == "firestore":
        repo = FirestoreRepo(model, collection, settings)
    elif settings.vl_store == "sqlite":
        path = settings.vl_data_dir / f"store-{collection.split('.')[0]}.sqlite"
        db = _sqlite_files.get(path)
        if db is None:
            db = _sqlite_files[path] = _SqliteFile(path)
        repo = SqliteRepo(model, collection, db)
    else:
        repo = MemoryRepo(model, collection)
    _repos[collection] = repo
    return cast(Repo[T], repo)


async def reset_repos(prefix: str) -> None:
    """Empty every collection under `prefix` in place (demo reset of one service)."""
    for name, repo in list(_repos.items()):
        if name.startswith(prefix):
            await repo.clear()


def reset_memory_repos(prefix: str | None = None) -> None:
    """Tests: forget every repo (and close nothing; each test gets fresh files and loops)."""
    if prefix is None:
        _repos.clear()
        _sqlite_files.clear()
        return
    for name, repo in _repos.items():
        if name.startswith(prefix) and isinstance(repo, MemoryRepo):
            repo._docs.clear()


__all__ = [
    "FirestoreRepo",
    "MemoryRepo",
    "Repo",
    "SqliteRepo",
    "get_repo",
    "reset_memory_repos",
    "reset_repos",
]
