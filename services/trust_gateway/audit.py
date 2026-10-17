"""Append-only, hash-chained audit log: hash = sha256(prev_hash + canonical(record - hash))."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from vl_core.crypto import canonical_json, sha256_hex
from vl_core.schemas import AuditRecord, AuditVerifyResult
from vl_core.store import Repo

GENESIS_HASH = "0" * 64


def chain_hash(record: AuditRecord) -> str:
    body = record.model_dump(mode="json", exclude={"hash"})
    return sha256_hex(record.prev_hash.encode("ascii") + canonical_json(body))


def _doc_id(seq: int) -> str:
    return f"{seq:012d}"


class AuditLog:
    """Single writer per process (run the gateway with max-instances=1 in the MVP)."""

    def __init__(self, repo: Repo[AuditRecord]) -> None:
        self._repo = repo
        self._lock = asyncio.Lock()
        self._head: tuple[int, str] | None = None

    async def _load_head(self) -> tuple[int, str]:
        if self._head is None:
            records = await self.records()
            self._head = (records[-1].seq, records[-1].hash) if records else (-1, GENESIS_HASH)
        return self._head

    async def append(
        self,
        *,
        actor_agent_id: str,
        on_behalf_of: str,
        action: str,
        target: str,
        payload_hash: str,
        config_hash: str,
        outcome: Literal["ok", "denied", "error"],
    ) -> AuditRecord:
        async with self._lock:
            seq, prev_hash = await self._load_head()
            draft = AuditRecord(
                seq=seq + 1,
                ts=datetime.now(UTC),
                actor_agent_id=actor_agent_id,
                on_behalf_of=on_behalf_of,
                action=action,
                target=target,
                payload_hash=payload_hash,
                config_hash=config_hash,
                outcome=outcome,
                prev_hash=prev_hash,
                hash="",
            )
            record = draft.model_copy(update={"hash": chain_hash(draft)})
            await self._repo.put(_doc_id(record.seq), record)
            self._head = (record.seq, record.hash)
            return record

    async def records(self) -> list[AuditRecord]:
        return sorted(await self._repo.query(), key=lambda r: r.seq)

    async def verify(self) -> AuditVerifyResult:
        prev_hash = GENESIS_HASH
        records = await self.records()
        for expected_seq, record in enumerate(records):
            if (
                record.seq != expected_seq
                or record.prev_hash != prev_hash
                or record.hash != chain_hash(record)
            ):
                return AuditVerifyResult(
                    ok=False, checked=expected_seq, first_broken_seq=expected_seq
                )
            prev_hash = record.hash
        return AuditVerifyResult(ok=True, checked=len(records))

    def forget_head(self) -> None:
        self._head = None
