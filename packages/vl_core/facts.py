"""Did she say it? Deterministic checks of listing facts against the vendor's own words, shared by
the retailer's Listing Enricher (to record where a fact came from) and her agent (to decide whether
it may approve on her behalf). No model."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(*texts: str) -> set[Decimal]:
    """Every number written in the texts ("₹220", "40 pieces", "42,5" → 220, 40, 42.5)."""
    found: set[Decimal] = set()
    for text in texts:
        for raw in _NUMBER.findall(text or ""):
            try:
                found.add(Decimal(raw.replace(",", ".")).normalize())
            except InvalidOperation:
                continue
    return found


def said_number(value: Decimal | str | int | float, *texts: str) -> bool:
    try:
        return Decimal(str(value)).normalize() in numbers_in(*texts)
    except InvalidOperation:
        return False


def mentions(phrase: str, *texts: str) -> bool:
    """The phrase appears in the texts as whole words, ignoring case."""
    words = phrase.casefold().split()
    if not words:
        return False
    pattern = r"\b" + r"\s+".join(map(re.escape, words)) + r"\b"
    return any(re.search(pattern, (t or "").casefold()) for t in texts)
