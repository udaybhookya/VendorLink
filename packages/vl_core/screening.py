"""Prompt-injection screening shared by the gateway (documents, relayed messages) and the
Liaison (people's free-text answers). Heuristic here; Model Armor for deploy."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from typing import Any, Protocol

from vl_core.settings import Settings

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "ignore_instructions": r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|"
        r"earlier|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b",
        "role_override": r"\byou are now\b|\bact as (an? )?(admin|administrator|system)\b",
        "system_prompt": r"\bsystem prompt\b|\bdeveloper mode\b",
        "forced_approval": r"\b(approve|verify|accept|whitelist)\b.{0,40}\b(immediately|"
        r"automatically|without (review|checks?|verification))\b",
        "markup": r"<\s*(script|iframe)\b",
        "ignore_instructions_id": r"\babaikan\b.{0,30}\b(instruksi|perintah|aturan)\b",
        "ignore_instructions_te": r"సూచనలను\s*విస్మరించ",
    }.items()
}

EDITING_SOFTWARE = (b"Photoshop", b"GIMP", b"Snapseed", b"PicsArt")


def iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


class Screener(Protocol):
    async def scan_text(self, text: str) -> list[str]: ...

    def extract_text(self, data: bytes, content_type: str, filename: str) -> str: ...

    def scan_image(self, data: bytes) -> list[str]: ...


def heuristic_findings(text: str) -> list[str]:
    return [f"injection:{name}" for name, rx in INJECTION_PATTERNS.items() if rx.search(text)]


class HeuristicScreener:
    async def scan_text(self, text: str) -> list[str]:
        return heuristic_findings(text)

    def extract_text(self, data: bytes, content_type: str, filename: str) -> str:
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if content_type.startswith("text/") or content_type in ("application/json", "text/csv"):
            return data.decode("utf-8", errors="replace")
        return ""

    def scan_image(self, data: bytes) -> list[str]:
        head = data[:65536]
        return [
            f"image:edited_with_{name.decode().lower()}"
            for name in EDITING_SOFTWARE
            if name in head
        ]


def _matched_filters(result: dict[str, Any]) -> list[str]:
    """Names of Model Armor filters whose result says MATCH_FOUND (any nesting depth)."""

    def matched(value: Any) -> bool:
        if isinstance(value, dict):
            return value.get("match_state") == "MATCH_FOUND" or any(
                matched(v) for v in value.values()
            )
        if isinstance(value, list):
            return any(matched(v) for v in value)
        return False

    filters = result.get("filter_results") or {}
    return sorted(name for name, value in filters.items() if matched(value))


class ModelArmorScreener(HeuristicScreener):
    """Model Armor (prompt injection, jailbreak, malicious URLs, RAI filters) on every text, on
    top of the heuristic patterns, which also cover our Telugu and Bahasa phrasings. Fails
    closed: if Model Armor cannot be reached the text is reported, not waved through."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        template = settings.model_armor_template
        if not template:
            raise ValueError("VL_SCREEN=model_armor needs MODEL_ARMOR_TEMPLATE")
        location = settings.google_cloud_location or "us-central1"
        if not template.startswith("projects/"):
            template = (
                f"projects/{settings.google_cloud_project}/locations/{location}"
                f"/templates/{template}"
            )
        self.template = template
        if client is None:
            from google.cloud import modelarmor_v1

            client = modelarmor_v1.ModelArmorAsyncClient(
                client_options={"api_endpoint": f"modelarmor.{location}.rep.googleapis.com"}
            )
        self._client = client

    async def scan_text(self, text: str) -> list[str]:
        findings = heuristic_findings(text)
        if not text.strip():
            return findings
        try:
            response = await self._client.sanitize_user_prompt(
                request={"name": self.template, "user_prompt_data": {"text": text}}
            )
        except Exception:
            return [*findings, "screening:unavailable"]
        result = getattr(response, "sanitization_result", response)
        as_dict = result if isinstance(result, dict) else type(result).to_dict(result)
        return findings + [f"model_armor:{name}" for name in _matched_filters(as_dict)]


def get_screener(settings: Settings) -> Screener:
    if settings.vl_screen == "model_armor":
        return ModelArmorScreener(settings)
    return HeuristicScreener()


def screen_text(text: str) -> list[str]:
    """Heuristic findings for one piece of text (empty when clean); synchronous, for the
    Liaison's short free-text answers."""
    return heuristic_findings(text)
