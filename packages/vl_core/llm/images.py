"""Image generation behind an interface: an AI-enhanced version of the vendor's own product photo,
from a regeneration prompt. Gemini image model when IMAGE_MODEL is set; none otherwise, and the
prompts then serve as reshoot tips. Never a stock or invented product: the photo is the input."""

from __future__ import annotations

import time
from typing import Protocol

from vl_core.llm.client import ImageInput
from vl_core.llm.usage import record_usage
from vl_core.obs import get_logger
from vl_core.settings import Settings, get_settings

log = get_logger("llm.images")


class ImageGenError(Exception):
    pass


class ImageGen(Protocol):
    model: str

    async def edit(self, prompt: str, photo: ImageInput, aspect_ratio: str = "1:1") -> bytes:
        """Return image bytes (PNG or JPEG) for `prompt` applied to `photo`, in `aspect_ratio`."""
        ...


class GeminiImageGen:
    def __init__(self, model: str, settings: Settings) -> None:
        from google import genai

        self.model = model
        vertex = settings.google_genai_use_vertexai
        self._client = genai.Client(
            vertexai=vertex,
            project=(settings.google_cloud_project or None) if vertex else None,
            location=(settings.google_cloud_location or None) if vertex else None,
        )

    async def edit(self, prompt: str, photo: ImageInput, aspect_ratio: str = "1:1") -> bytes:
        from google.genai import types

        started = time.perf_counter()
        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=photo.data, mime_type=photo.mime_type),
                    types.Part.from_text(text=prompt),
                ],
            ),
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )
        record_usage(response.usage_metadata)
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.inline_data and part.inline_data.data:
                    log.info(
                        "image generated",
                        model=self.model,
                        ms=round((time.perf_counter() - started) * 1000),
                    )
                    return part.inline_data.data
        raise ImageGenError("the model returned no image")


_override: ImageGen | None = None


def set_image_gen_override(gen: ImageGen | None) -> None:
    global _override
    _override = gen


def get_image_gen(settings: Settings | None = None) -> ImageGen | None:
    """None when no image model is configured: callers offer the prompts as reshoot tips."""
    if _override is not None:
        return _override
    settings = settings or get_settings()
    if not settings.image_model:
        return None
    return GeminiImageGen(settings.image_model, settings)
