"""broadcast_os/graphics/_canvas.py — shared drawing primitives for every
graphics generator and scene renderer. Kept private (leading underscore) —
not one of the spec's named packages, just the common helper layer that
stops the 8 generators from re-implementing the same font/gradient/glow
code eight times.
"""
from __future__ import annotations

import functools
import math
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from broadcast_os.config import CONFIG

W, H = CONFIG.width, CONFIG.height


@functools.lru_cache(maxsize=64)
def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def font_bold(size: int) -> ImageFont.FreeTypeFont:
    return font(CONFIG.font_bold, size)


def font_regular(size: int) -> ImageFont.FreeTypeFont:
    return font(CONFIG.font_regular, size)


def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 2


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_colour(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(lerp(a[i], b[i], t)) for i in range(3))


@functools.lru_cache(maxsize=1)
def newsroom_bg() -> Image.Image:
    """The shared WNX24 studio background — loaded once and reused across
    every frame/scene that needs it, per CONFIG.bg_path."""
    try:
        return Image.open(CONFIG.bg_path).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception:
        return new_rgb()


def _key_out_black(path: str) -> Image.Image | None:
    """Key a solid-black export background out to transparency (both WNX24
    logo exports are plain RGB, no alpha channel — without this they render
    as a black square/rectangle over the navy scenes)."""
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return None

    import numpy as np
    arr = np.asarray(img, dtype=np.int16)
    luminance = arr.sum(axis=2)  # 0..765
    low, high = 18, 60  # feathered ramp so the cutout edge isn't a hard line
    alpha = np.clip((luminance - low) / (high - low), 0.0, 1.0) * 255
    rgba = np.dstack([arr.astype(np.uint8), alpha.astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


@functools.lru_cache(maxsize=1)
def wnx24_logo() -> Image.Image | None:
    """The WNX24 globe+wordmark logo — used for the intro sting and the
    headline card's small corner mark."""
    return _key_out_black(CONFIG.logo_path)


@functools.lru_cache(maxsize=1)
def wnx24_icon() -> Image.Image | None:
    """The WNX24 sign-off icon — displayed at the end of every broadcast
    (outro scene), per assets/WNX24_icon.png."""
    return _key_out_black(CONFIG.icon_path)


def new_rgba() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def new_rgb(colour: tuple = CONFIG.NAVY) -> Image.Image:
    return Image.new("RGB", (W, H), colour)


def paste_rgba(base: Image.Image, overlay: Image.Image, pos: tuple = (0, 0)) -> Image.Image:
    base = base.convert("RGBA")
    base.alpha_composite(overlay, pos)
    return base.convert("RGB")


def vertical_gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)], fill=lerp_colour(top, bottom, t))
    return img


def wrap_lines(text: str, width: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap((text or "").strip(), width=width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1].rstrip()
        if len(last) >= width:
            last = last[: width - 1].rstrip()
        lines[-1] = last + "…"
    return lines


def centred_x(draw: ImageDraw.ImageDraw, text: str, fnt, box_x0: int = 0, box_x1: int = W) -> int:
    bbox = draw.textbbox((0, 0), text, font=fnt)
    return box_x0 + ((box_x1 - box_x0) - (bbox[2] - bbox[0])) // 2


def glow_dot(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int, colour: tuple, layers: int = 4) -> None:
    """Cheap radial glow: concentric translucent circles, largest+faintest
    first. Cheaper than a Gaussian blur pass repeated per frame."""
    for i in range(layers, 0, -1):
        r = radius * (1 + i * 0.6)
        alpha = int(70 / i)
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(colour[0], colour[1], colour[2], alpha),
        )
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(*colour, 255),
    )


def soft_glow(img: Image.Image, radius: int = 12) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius))


def orbit_point(cx: float, cy: float, rx: float, ry: float, angle_rad: float) -> tuple:
    return (cx + rx * math.cos(angle_rad), cy + ry * math.sin(angle_rad))


def rounded_panel(
    draw: ImageDraw.ImageDraw, box: tuple, fill: tuple, radius: int = 10, outline=None, width: int = 0
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def slide_in_x(progress: float, from_x: float, to_x: float) -> float:
    return lerp(from_x, to_x, ease_out(progress))
