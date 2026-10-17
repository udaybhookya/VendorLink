from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from vl_core.llm.usage import record_usage
from vl_core.obs import get_logger
from vl_core.settings import Settings, get_settings

log = get_logger("llm")


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    """Either a validated `value`, or `needs_review` with the reason. Never a silent default."""

    value: T | None
    needs_review: bool
    error: str | None = None

    @classmethod
    def ok(cls, value: T) -> LlmResult[T]:
        return cls(value=value, needs_review=False)

    @classmethod
    def review(cls, error: str) -> LlmResult[T]:
        return cls(value=None, needs_review=True, error=error)


class Llm(Protocol):
    async def generate_json[T: BaseModel](
        self, prompt: str, schema: type[T], images: Sequence[ImageInput] = ()
    ) -> LlmResult[T]: ...


class JsonLlm(ABC):
    """Validation and retry policy shared by every backend; subclasses only implement `_call`."""

    max_attempts = 2  # first try + one retry
    model = "?"  # for logs

    @abstractmethod
    async def _call(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[ImageInput]
    ) -> str: ...

    async def generate_json[T: BaseModel](
        self, prompt: str, schema: type[T], images: Sequence[ImageInput] = ()
    ) -> LlmResult[T]:
        json_schema = schema.model_json_schema()
        attempt_prompt = prompt
        error = ""
        started = time.perf_counter()
        for attempt in range(self.max_attempts):
            try:
                raw = await self._call(attempt_prompt, json_schema, images)
                value = schema.model_validate_json(raw)
                log.info(
                    f"llm {schema.__name__} ok",
                    model=self.model,
                    attempt=attempt + 1,
                    images=len(images) or None,
                    ms=_ms(started),
                )
                return LlmResult.ok(value)
            except ValidationError as exc:
                error = f"schema validation failed: {exc.errors(include_input=False)}"
            except Exception as exc:  # backend/network errors also end in review
                error = f"llm call failed: {type(exc).__name__}: {exc}"
            log.warning(
                f"llm {schema.__name__} rejected",
                model=self.model,
                attempt=attempt + 1,
                error=error[:300],
            )
            attempt_prompt = (
                f"{prompt}\n\nYour previous answer was rejected ({error}). "
                "Reply again with JSON that matches the schema exactly."
            )
        log.warning(f"llm {schema.__name__} needs review", model=self.model, ms=_ms(started))
        return LlmResult.review(error)


class GeminiLlm(JsonLlm):
    def __init__(self, model: str, settings: Settings) -> None:
        if not model:
            raise ValueError("model name is empty; set FLASH_MODEL / PRO_MODEL")
        from google import genai

        self._model = self.model = model
        self._client = genai.Client(
            vertexai=settings.google_genai_use_vertexai,
            project=(settings.google_cloud_project or None)
            if settings.google_genai_use_vertexai
            else None,
            location=(settings.google_cloud_location or None)
            if settings.google_genai_use_vertexai
            else None,
        )

    async def _call(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[ImageInput]
    ) -> str:
        from google.genai import types

        contents: list[Any] = [
            types.Part.from_bytes(data=img.data, mime_type=img.mime_type) for img in images
        ]
        contents.append(
            f"{prompt}\n\nRespond with JSON matching this schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        record_usage(response.usage_metadata)
        return str(response.text or "")


class FakeLlm(JsonLlm):
    """Replays scripted raw responses in order. For tests and offline evals."""

    model = "fake"

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def _call(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[ImageInput]
    ) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise RuntimeError("FakeLlm has no scripted responses left")
        return self._responses.pop(0)


_override: Llm | None = None


def set_llm_override(llm: Llm | None) -> None:
    """Make `get_llm` return `llm` (tests and offline evals). None restores the real backend."""
    global _override
    _override = llm


class UnconfiguredLlm:
    """Used when no model is configured: every call ends in "needs review", never a guess."""

    async def generate_json[T: BaseModel](
        self, prompt: str, schema: type[T], images: Sequence[ImageInput] = ()
    ) -> LlmResult[T]:
        return LlmResult.review("no model configured (set FLASH_MODEL / PRO_MODEL)")


def get_llm(tier: Literal["flash", "pro"], settings: Settings | None = None) -> Llm:
    if _override is not None:
        return _override
    settings = settings or get_settings()
    if not (settings.flash_model if tier == "flash" else settings.pro_model):
        return UnconfiguredLlm()
    model = settings.flash_model if tier == "flash" else settings.pro_model
    return GeminiLlm(model, settings)


def load_prompt(agent: str, lang: str, root: Path | None = None) -> str:
    """Read `config/prompts/<agent>.<lang>.md`. Missing prompts are an error, not a fallback."""
    root = root or get_settings().vl_config_dir / "prompts"
    path = root / f"{agent}.{lang}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt for agent {agent!r} in language {lang!r}: {path}")
    return path.read_text(encoding="utf-8")


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
