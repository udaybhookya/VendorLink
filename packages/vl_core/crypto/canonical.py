from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        text = format(obj.normalize(), "f")
        return "0" if text in ("-0", "") else text  # same form as schema Dec fields
    if isinstance(obj, Enum):
        return _to_jsonable(obj.value)
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_to_jsonable(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, no NaN/Infinity.

    Pydantic models are dumped in JSON mode, so Decimals become strings and datetimes ISO 8601.
    """
    return json.dumps(
        _to_jsonable(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    """sha256 hex of the canonical JSON of `obj`."""
    return sha256_hex(canonical_json(obj))


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
