"""Fixture-driven registry checks with deterministic failure cases."""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from vl_core.schemas import RegistryCheckRequest, RegistryCheckResult

FIXTURES = Path(__file__).parent / "fixtures"


@cache
def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _norm(name: str) -> set[str]:
    text = unicodedata.normalize("NFKC", name).casefold()
    return {token for token in text.replace(".", " ").split() if len(token) > 1}


def names_match(given: str, registered: str) -> bool:
    a, b = _norm(given), _norm(registered)
    return bool(a) and (a == b or a <= b or b <= a)


def check(registry: str, req: RegistryCheckRequest, today: date) -> RegistryCheckResult:
    table: dict[str, Any] = fixture("registries").get(registry, {})
    if registry in ("id", "bank"):
        table = table.get(req.doc_type, {})
    record = table.get(req.number.replace(" ", ""))
    if record is None:
        return RegistryCheckResult(
            doc_type=req.doc_type,
            status="fail",
            code="NOT_FOUND",
            detail=f"{req.doc_type} number not in the simulated registry",
        )
    registered = str(record["name"])
    valid_until = date.fromisoformat(record["valid_until"]) if "valid_until" in record else None
    if not names_match(req.name, registered):
        return RegistryCheckResult(
            doc_type=req.doc_type,
            status="fail",
            code="NAME_MISMATCH",
            detail="name differs from the registry",
            registered_name=registered,
            valid_until=valid_until,
        )
    if valid_until is not None and valid_until < today:
        return RegistryCheckResult(
            doc_type=req.doc_type,
            status="fail",
            code="EXPIRED",
            detail=f"expired on {valid_until.isoformat()}",
            registered_name=registered,
            valid_until=valid_until,
        )
    return RegistryCheckResult(
        doc_type=req.doc_type,
        status="pass",
        code="OK",
        registered_name=registered,
        valid_until=valid_until,
    )
