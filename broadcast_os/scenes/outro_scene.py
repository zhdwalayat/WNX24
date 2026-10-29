"""broadcast_os/scenes/outro_scene.py — Scene 8: OUTRO.

Plays inside the studio's globe-screen content box like every other
scene — the newsroom stays visible right up to the final sign-off, which
only then fades to black (a standard broadcast close, not a mid-show cut
away from the studio).
"""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import newsroom_bg, wnx24_icon
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import render_frames

_RING_PERIOD = 12.0
_FADE_OUT_SECS = 1.0


def _compose(t: float, duration: float) -> Image.Image:
    cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
    box_w, box_h = cx1 - cx0, cy1 - cy0
    cx, cy = cx0 + box_w // 2, cy0 + int(box_h * 0.36)

    base = newsroom_bg().copy()
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([cx0, cy0, cx1, cy1], fill=(4, 8, 20, 175))

    ring_angle = (t / _RING_PERIOD) * 360.0
    for rx, ry, colour, offset in [
        (min(170, box_w * 0.21), 65, CONFIG.RED, 0),
        (min(195, box_w * 0.24), 76, CONFIG.ELECTRIC, 50),
    ]:
        bbox = [cx - rx, cy - ry, cx + rx, cy + ry]
        start = ring_angle + offset
        draw.arc(bbox, start=start, end=start + 300, fill=(*colour, 255), width=3)

    logo = wnx24_icon()
    logo_alpha = int(255 * min(1.0, t / 0.8))
    if logo is not None:
        size = 140
        lg = logo.resize((size, size), Image.LANCZOS)
        a = lg.split()[3].point(lambda p: int(p * logo_alpha / 255))
        lg.putalpha(a)
        overlay.alpha_composite(lg, (cx - size // 2, cy - size // 2))

    frame = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")

    # Fade to black over the final second — a standard broadcast close,
    # applied after the studio content, not instead of it.
    if t > duration - _FADE_OUT_SECS:
        fade = min(1.0, (t - (duration - _FADE_OUT_SECS)) / _FADE_OUT_SECS)
        black = Image.new("RGB", frame.size, (0, 0, 0))
        frame = Image.blend(frame, black, fade)

    return frame


def render(package: ScenePackage, fps: int, global_start_time: float) -> Iterator[np.ndarray]:
    directive = package.directive
    duration = package.duration
    return render_frames(
        duration, fps, lambda t: _compose(t, duration),
        motion=directive.camera_motion, speed=directive.motion_speed,
    )
