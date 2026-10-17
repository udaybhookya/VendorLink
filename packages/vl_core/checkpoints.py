"""Durable state for paused jobs (LangGraph checkpointers), behind one switch.

    VL_CHECKPOINTS=memory    tests
    VL_CHECKPOINTS=sqlite    local dev (file under VL_DATA_DIR)
    VL_CHECKPOINTS=postgres  deploy: Cloud SQL, DSN from Secret Manager (VL_CHECKPOINT_DSN)

Postgres is LangGraph's maintained saver and safe across Cloud Run instances; SQLite is not
(ephemeral, per-instance disk), and a custom Firestore saver would be unmaintained code on the
path that decides whether a paused job can resume.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from vl_core.settings import Settings, get_settings


@asynccontextmanager
async def open_checkpointer(settings: Settings | None = None) -> AsyncIterator[Any]:
    settings = settings or get_settings()
    if settings.vl_checkpoints == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
    elif settings.vl_checkpoints == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        settings.vl_data_dir.mkdir(parents=True, exist_ok=True)
        path = settings.vl_data_dir / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
    else:
        if not settings.vl_checkpoint_dsn:
            raise RuntimeError("VL_CHECKPOINTS=postgres needs VL_CHECKPOINT_DSN")
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.vl_checkpoint_dsn) as saver:
            await saver.setup()
            yield saver
