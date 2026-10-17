"""Deterministic photo measurements and previews (Pillow). No model: size, sharpness and
brightness are measured, so the catalogue checks that matter are reproducible."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

# Sharpness is measured on a copy of fixed size, so the score does not depend on resolution.
_MEASURE_PX = 512
_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


@dataclass(frozen=True)
class PhotoMetrics:
    width: int
    height: int
    sharpness: float  # edge strength relative to contrast, ~0-100: low = blurry, at any light
    brightness: float  # mean luminance, 0..255


class PhotoError(ValueError):
    """The bytes are not a readable image."""


def _open(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, ValueError) as exc:  # includes UnidentifiedImageError and truncated data
        raise PhotoError(str(exc)) from exc
    return image


def measure(data: bytes) -> PhotoMetrics:
    image = _open(data)
    gray = image.convert("L")
    small = gray.copy()
    small.thumbnail((_MEASURE_PX, _MEASURE_PX))
    edges = small.filter(_LAPLACIAN)
    edges = edges.crop((2, 2, max(3, edges.width - 2), max(3, edges.height - 2)))  # no border ring
    edge_strength = ImageStat.Stat(edges).stddev[0]  # spread of the Laplacian
    contrast = ImageStat.Stat(small).stddev[0]
    return PhotoMetrics(
        width=image.width,
        height=image.height,
        # normalised by contrast, so a dim but sharp photo is not called blurry
        sharpness=round(100 * edge_strength / max(contrast, 1.0), 1),
        brightness=round(ImageStat.Stat(gray).mean[0], 1),
    )


def preview(data: bytes, max_px: int = 384, quality: int = 80) -> bytes:
    """A small JPEG for the apps (the full image stays in the blob store)."""
    image = _open(data).convert("RGB")
    image.thumbnail((max_px, max_px))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def as_jpeg(data: bytes, max_px: int = 1024, quality: int = 85) -> bytes:
    """Normalise a generated image (often a large PNG) to a catalogue-sized JPEG."""
    return preview(data, max_px=max_px, quality=quality)


def mime_of(data: bytes) -> str:
    """The image's MIME type from its bytes (JPEG, PNG, WEBP …)."""
    fmt = (_open(data).format or "JPEG").lower()
    return f"image/{'jpeg' if fmt in ('jpg', 'jpeg', 'mpo') else fmt}"


def auto_enhance(
    data: bytes, min_brightness: float = 120.0, max_brightness: float = 235.0, quality: int = 90
) -> bytes:
    """A classic, deterministic clean-up of her own photo: exposure (gamma) and contrast, a gentle
    sharpen, and catalogue framing (very wide or tall photos are centre-cropped to 4:3, then
    padded to a square with the photo's own background colour). Nothing is added or invented and
    the photo is never upscaled: this is editing, not AI."""
    image = ImageOps.exif_transpose(_open(data)).convert("RGB")
    image = _frame(image)
    before = _brightness(image)
    stretched = ImageOps.autocontrast(image, cutoff=1)
    if before < min_brightness or _brightness(stretched) >= before - 10:
        image = stretched  # a contrast stretch must not darken a photo that was not dark
    mean = _brightness(image) / 255
    if mean * 255 < min_brightness:  # too dark: lift the shadows towards a daylight look
        gamma = max(0.35, math.log(0.62) / math.log(max(mean, 0.01)))
        image = image.point([round(255 * (v / 255) ** gamma) for v in range(256)] * 3)
    elif mean * 255 > max_brightness:  # washed out: bring it back a little
        image = ImageEnhance.Brightness(image).enhance(max(0.8, 220.0 / (mean * 255)))
    image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))
    out = io.BytesIO()
    _square(image).save(out, format="JPEG", quality=quality)
    return out.getvalue()


def _brightness(image: Image.Image) -> float:
    return float(ImageStat.Stat(image.convert("L")).mean[0])


def _frame(image: Image.Image, max_ratio: float = 4 / 3) -> Image.Image:
    """Centre-crop a very wide or tall photo to at most 4:3 (products are usually centred)."""
    w, h = image.size
    if w / h > max_ratio:
        keep = round(h * max_ratio)
        return image.crop(((w - keep) // 2, 0, (w - keep) // 2 + keep, h))
    if h / w > max_ratio:
        keep = round(w * max_ratio)
        return image.crop((0, (h - keep) // 2, w, (h - keep) // 2 + keep))
    return image


def _square(image: Image.Image) -> Image.Image:
    """Pad to a square with the median colour found just inside the photo's edges."""
    w, h = image.size
    if w == h:
        return image
    m = max(2, min(w, h) // 40)  # step in past any thin border line
    bands = [
        image.crop((m, m, w - m, 2 * m)),
        image.crop((m, h - 2 * m, w - m, h - m)),
        image.crop((m, m, 2 * m, h - m)),
        image.crop((w - 2 * m, m, w - m, h - m)),
    ]
    medians = [ImageStat.Stat(b).median for b in bands]
    fill = tuple(sorted(c[i] for c in medians)[len(medians) // 2] for i in range(3))
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), fill)
    canvas.paste(image, ((side - w) // 2, (side - h) // 2))
    return canvas
