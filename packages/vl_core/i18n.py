"""User-facing strings per language from `config/i18n/<lang>.json`. No strings in code."""

from __future__ import annotations

import json
import re
import string
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from vl_core.settings import get_settings


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@lru_cache(maxsize=16)
def _load(lang: str, root: Path) -> dict[str, Any]:
    path = root / f"{lang}.json"
    if not path.exists():
        raise FileNotFoundError(f"no strings for language {lang!r}: {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


class Strings:
    def __init__(self, lang: str, root: Path | None = None) -> None:
        self.lang = lang
        self._data = _load(lang, root or get_settings().vl_config_dir / "i18n")

    def get(self, key: str) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"missing string {key!r} for {self.lang!r}")
            node = node[part]
        return node

    def t(self, key: str, **params: Any) -> str:
        """Render `key` with `params`; unknown placeholders are left visible, never dropped."""
        template = self.get(key)
        if not isinstance(template, str):
            raise KeyError(f"{key!r} is not a string")
        return string.Formatter().vformat(template, (), _SafeDict(params))

    def words(self, key: str) -> list[str]:
        value = self.get(key)
        return [str(v) for v in value] if isinstance(value, list) else []


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _contains(text: str, word: str) -> bool:
    word = _normalise(word)
    if word.isascii():
        return re.search(rf"(?<![\w']){re.escape(word)}(?![\w'])", text) is not None
    return word in text  # scripts like Telugu: no reliable \b; match the word as written


def is_affirmative(text: str, strings: Strings) -> bool:
    """A clear yes: an affirmative word and no negative word (deterministic, no LLM)."""
    norm = _normalise(text)
    if not norm or any(_contains(norm, w) for w in strings.words("negatives")):
        return False
    return any(_contains(norm, w) for w in strings.words("affirmatives"))


def is_negative(text: str, strings: Strings) -> bool:
    norm = _normalise(text)
    return any(_contains(norm, w) for w in strings.words("negatives"))
