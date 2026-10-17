"""vl_core.imaging: deterministic measurements and the classic (non-AI) photo clean-up."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from vl_core.imaging import PhotoError, auto_enhance, measure, mime_of


def photo(size: tuple[int, int] = (900, 600), light: int = 225, fmt: str = "JPEG") -> bytes:
    """A jar on a softly lit wall: a vertical gradient around `light`, like a real room."""
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        v = max(0, min(255, light - 20 + round(40 * y / size[1])))
        draw.line([(0, y), (size[0], y)], fill=(v, max(0, v - 5), max(0, v - 15)))
    ImageDraw.Draw(image).rectangle(
        [size[0] // 3, size[1] // 4, 2 * size[0] // 3, 3 * size[1] // 4], fill=(150, 110, 190)
    )
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def test_a_dark_photo_is_lifted_and_framed_square() -> None:
    dark = photo(light=25)
    before, after = measure(dark), measure(auto_enhance(dark))
    assert after.brightness > before.brightness + 40
    assert after.width == after.height == 800  # 900×600 is 3:2: cropped to 4:3, padded square


def test_a_banner_is_cropped_to_4_3_before_padding() -> None:
    after = measure(auto_enhance(photo(size=(1280, 460))))
    assert after.width == after.height == round(460 * 4 / 3)


def test_a_bright_studio_photo_is_not_darkened() -> None:
    studio = photo(light=228)
    assert measure(auto_enhance(studio)).brightness >= measure(studio).brightness - 15


def test_never_upscaled() -> None:
    small = photo(size=(400, 300))
    assert measure(auto_enhance(small)).width <= 400


def test_mime_and_unreadable() -> None:
    assert mime_of(photo()) == "image/jpeg" and mime_of(photo(fmt="PNG")) == "image/png"
    with pytest.raises(PhotoError):
        auto_enhance(b"not an image")
