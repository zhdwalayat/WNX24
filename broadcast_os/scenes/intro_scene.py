"""broadcast_os/scenes/intro_scene.py — Scene 1: INTRO.

Plays inside the studio's globe-screen content box like every other
scene — the newsroom (side menu, markets panel, floor) stays visible
throughout the whole broadcast, including the open; nothing ever cuts to
a flat, studio-less background.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterator

import numpy as np
from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import (
    ease_out,
    font_bold,
    font_regular,
    newsroom_bg,
    wnx24_logo,
)
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import render_frames

_RING_PERIOD = 12.0  # seconds per full rotation, per motion principles
_rng = random.Random(7)
_PARTICLES = [
    (_rng.uniform(0.05, 0.95), _rng.uniform(0.05, 0.95), _rng.uniform(0.6, 1.6))
    for _ in range(40)
]


def _centred_x_box(draw: ImageDraw.ImageDraw, text: str, font, x0: int, x1: int) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return x0 + ((x1 - x0) - (bbox[2] - bbox[0])) // 2


def _compose(t: float) -> Image.Image:
    cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
    box_w, box_h = cx1 - cx0, cy1 - cy0
    cx, cy = cx0 + box_w // 2, cy0 + int(box_h * 0.38)

    base = newsroom_bg().copy()
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([cx0, cy0, cx1, cy1], fill=(4, 8, 20, 175))

    # Blue floating particles, gentle drift — confined to the content box
    for fx, fy, sz in _PARTICLES:
        px = cx0 + fx * box_w + math.sin(t * 0.5 + fx * 10) * 6
        py = cy0 + fy * box_h + math.cos(t * 0.4 + fy * 10) * 6
        r = sz
        draw.ellipse([px - r, py - r, px + r, py + r], fill=(60, 110, 200, 255))

    # Orbital rings — red + blue, slow rotation, sized to fit the box
    ring_angle = (t / _RING_PERIOD) * 360.0
    for rx, ry, colour, offset in [
        (min(180, box_w * 0.22), 70, CONFIG.RED, 0),
        (min(210, box_w * 0.26), 82, CONFIG.ELECTRIC, 40),
    ]:
        bbox = [cx - rx, cy - ry, cx + rx, cy + ry]
        start = ring_angle + offset
        draw.arc(bbox, start=start, end=start + 300, fill=(*colour, 255), width=3)

    # Logo — fades + zooms in over first 2s
    logo_alpha = int(255 * min(1.0, t / 2.0))
    logo = wnx24_logo()
    if logo is not None and logo_alpha > 0:
        size = int(150 + 30 * ease_out(min(1.0, t / 2.0)))
        lg = logo.resize((size, size), Image.LANCZOS)
        a = lg.split()[3].point(lambda p: int(p * logo_alpha / 255))
        lg.putalpha(a)
        overlay.alpha_composite(lg, (cx - size // 2, cy - size // 2))
    else:
        wm_font = font_bold(64)
        draw.text(
            (_centred_x_box(draw, "WNX24", wm_font, cx0, cx1), cy - 40), "WNX24",
            fill=(*CONFIG.GOLD, 255), font=wm_font,
        )

    # Text stack — types in after logo settles, high-contrast colours only
    # (no coloured text directly on a dark panel — brand red is reserved
    # for fills/badges/icons, never small text on near-black).
    text_y = cy + 100
    reveal = max(0.0, min(1.0, (t - 1.8) / 1.2))

    title_font = font_bold(48)
    title = "WNX24"
    n1 = int(len(title) * min(1.0, reveal / 0.4))
    draw.text(
        (_centred_x_box(draw, title, title_font, cx0, cx1), text_y), title[:n1],
        fill=(*CONFIG.WHITE, 255), font=title_font,
    )

    sub_font = font_bold(22)
    sub = "GLOBAL NEWS."
    n2 = int(len(sub) * max(0.0, min(1.0, (reveal - 0.35) / 0.4)))
    draw.text(
        (_centred_x_box(draw, sub, sub_font, cx0, cx1), text_y + 58), sub[:n2],
        fill=(200, 205, 220, 255), font=sub_font,
    )

    tag_font = font_regular(20)
    tag = "POWERED BY INTELLIGENCE."
    n3 = int(len(tag) * max(0.0, min(1.0, (reveal - 0.65) / 0.4)))
    draw.text(
        (_centred_x_box(draw, tag, tag_font, cx0, cx1), text_y + 94), tag[:n3],
        fill=(*CONFIG.GOLD, 255), font=tag_font,
    )

    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


def render(package: ScenePackage, fps: int, global_start_time: float) -> Iterator[np.ndarray]:
    directive = package.directive
    return render_frames(
        package.duration, fps, _compose,
        motion=directive.camera_motion, speed=directive.motion_speed,
    )
