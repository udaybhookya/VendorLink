from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _decimal_str(value: Decimal) -> str:
    # Normalised so 300, 300.0 and 300.00 hash identically.
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


Dec = Annotated[Decimal, PlainSerializer(_decimal_str, return_type=str, when_used="json")]
"""Decimal that serialises to a normalised string in JSON (stable for hashing)."""

Money = Annotated[Dec, Field(ge=0)]
Qty = Annotated[Dec, Field(gt=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]

LocalizedText = dict[str, str]
"""Language code (BCP-47, e.g. "te", "id") → text."""

FALLBACK_LANGUAGE = "en"
"""The complete reference catalogue: used when a party has no language or a string is missing."""


class VLModel(BaseModel):
    """Base for every vl_core schema: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")
