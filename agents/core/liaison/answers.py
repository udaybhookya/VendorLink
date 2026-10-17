"""Reading a person's reply against what was asked. Deterministic; no LLM."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from vl_core.i18n import Strings
from vl_core.schemas import InfoRequest
from vl_core.screening import screen_text

QUOTE_START = re.compile(r"^(on .+wrote:|-{2,}\s*original message|from:\s)", re.IGNORECASE)
NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMY_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")


def reply_body(text: str) -> str:
    """The new part of a reply: drop quoted lines and everything after 'On … wrote:'."""
    lines = []
    for line in text.replace("\r", "").splitlines():
        if QUOTE_START.match(line.strip()):
            break
        if not line.lstrip().startswith(">"):
            lines.append(line)
    return "\n".join(lines).strip()


def _mentions(text: str, word: str) -> bool:
    word = word.casefold().strip()
    if not word:
        return False
    if word.isascii() and word[0].isalnum():
        return re.search(rf"(?<![\w']){re.escape(word)}(?![\w'])", text) is not None
    return word in text


def parse(text: str, request: InfoRequest, strings: Strings) -> tuple[bool, Any, str]:
    """(understood, value, note). Unclear or unsafe replies are never guessed."""
    body = reply_body(text)
    if not body:
        return False, None, "empty"
    if screen_text(body):
        return False, None, "unsafe"
    lowered = body.casefold()
    if request.answer_type == "choice":
        matched = {
            c.id
            for c in request.choices
            if any(_mentions(lowered, w) for w in [c.id, c.label, *c.words])
        }
        return (True, matched.pop(), "") if len(matched) == 1 else (False, None, "ambiguous")
    if request.answer_type == "number":
        if any(_mentions(lowered, w) for w in strings.words("negatives")):
            return True, "no", "declined"
        m = NUMBER.search(body)
        if m is None:
            return False, None, "no number"
        try:
            return True, str(Decimal(m.group(0).replace(",", "."))), ""
        except InvalidOperation:
            return False, None, "bad number"
    if request.answer_type == "date":
        if m := ISO_DATE.search(body):
            return True, date(int(m[1]), int(m[2]), int(m[3])).isoformat(), ""
        if m := DMY_DATE.search(body):
            return True, date(int(m[3]), int(m[2]), int(m[1])).isoformat(), ""
        return False, None, "no date"
    return True, body[:500], ""
