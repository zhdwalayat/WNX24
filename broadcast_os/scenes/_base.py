"""broadcast_os/scenes/_base.py — shared frame-loop and camera-motion helper
used by every scene renderer. Not one of the spec's named scene files —
the common plumbing that stops all 8 renderers from re-implementing the
same Ken-Burns crop-zoom and frame loop.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator

import numpy as np
from PIL import Image


def zoom_crop(img: Image.Image, scale: float, dx: float = 0.0, dy: float = 0.0) -> Image.Image:
    """Upscale `img` by `scale` around its centre (offset by dx, dy) and
    crop back to the original size — a general-purpose Ken Burns primitive."""
    w, h = img.size
    scale = max(1.0, scale)
    new_w, new_h = max(w, int(w * scale)), max(h, int(h * scale))
    if (new_w, new_h) != (w, h):
        scaled = img.resize((new_w, new_h), Image.LANCZOS)
    else:
        scaled = img

    cx = new_w / 2 + dx
    cy = new_h / 2 + dy
    left = max(0, min(new_w - w, cx - w / 2))
    top = max(0, min(new_h - h, cy - h / 2))
    return scaled.crop((int(left), int(top), int(left) + w, int(top) + h))


def apply_camera_motion(img: Image.Image, motion: str, speed: float, progress: float) -> Image.Image:
    """`progress` is 0..1 across the scene's duration. `speed` (0.01-0.05,
    per broadcast_os.agents.visual_director) sets motion intensity —
    "Camera: 2-3% movement maximum" per the BroadcastOS motion principles,
    so amplitudes below are deliberately small."""
    if motion in ("static", "", None):
        return img

    if motion in ("slow_zoom_in", "ken_burns_zoom"):
        scale = 1.0 + speed * 4.0 * progress
        return zoom_crop(img, scale)

    if motion in ("slow_zoom_out", "pull_back"):
        scale = 1.0 + speed * 4.0 * (1.0 - progress)
        return zoom_crop(img, scale)

    if motion == "static_drift":
        amp = speed * 250
        dx = math.sin(progress * math.pi * 2) * amp
        return zoom_crop(img, 1.015, dx=dx)

    if motion == "slow_orbit":
        amp = speed * 200
        dx = math.sin(progress * math.pi * 2) * amp
        dy = math.cos(progress * math.pi * 2) * amp * 0.35
        return zoom_crop(img, 1.0 + speed * 2.0, dx=dx, dy=dy)

    return img


def render_frames(
    duration: float,
    fps: int,
    compose_fn: Callable[[float], Image.Image],
    motion: str = "static",
    speed: float = 0.02,
) -> Iterator[np.ndarray]:
    """Call `compose_fn(t_local_seconds)` once per frame, apply the shared
    camera-motion post-process, and yield RGB uint8 arrays one at a time —
    a generator, not a materialised list, since duration is unbounded
    (long bulletin narration can run to minutes; holding every frame of
    the whole scene in memory at once is not an option)."""
    n_frames = max(1, round(duration * fps))
    for i in range(n_frames):
        t = i / fps
        progress = i / max(1, n_frames - 1)
        img = compose_fn(t).convert("RGB")
        img = apply_camera_motion(img, motion, speed, progress)
        yield np.asarray(img, dtype=np.uint8)


def cover_fit(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize + centre-crop to exactly (target_w, target_h) without
    distorting aspect ratio — CSS `object-fit: cover` equivalent."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def ticker_text_from_package(package) -> str | None:
    """The orchestrator computes the ticker text once per broadcast and
    attaches it as a GraphicSpec so every scene's TickerGenerator scrolls
    the same headlines instead of re-querying the DB per scene."""
    for spec in package.graphics:
        if spec.graphic_type == "ticker":
            return spec.data.get("text")
    return None


def summary_points_from_package(package) -> list[str] | None:
    for spec in package.graphics:
        if spec.graphic_type == "summary_points":
            return spec.data.get("points")
    return None


def composite_lower_third_and_ticker(
    base_img: Image.Image,
    lower_third_gen,
    ticker_gen,
    t_local: float,
    duration: float,
    global_t: float,
) -> Image.Image:
    """Standard newsroom furniture applied to every content scene:
    lower third (local scene time) + scrolling ticker (global broadcast
    time, so the scroll never resets between scenes)."""
    out = base_img.convert("RGBA")
    if lower_third_gen is not None:
        out.alpha_composite(lower_third_gen.frame(t_local, duration))
    if ticker_gen is not None:
        out.alpha_composite(ticker_gen.frame(global_t))
    return out.convert("RGB")


def editorial_fallback_frame(headline: str) -> Image.Image:
    """Same-style solid card used whenever a sourced image isn't
    available — matches agents/image_agent.py's own fallback-card look."""
    from PIL import ImageDraw

    from broadcast_os.graphics._canvas import H, font_bold, newsroom_bg, wrap_lines

    img = newsroom_bg().copy()
    draw = ImageDraw.Draw(img)
    font = font_bold(48)
    lines = wrap_lines(headline or "WNX24 News", width=26, max_lines=3)
    y = H // 2 - len(lines) * 30
    for line in lines:
        draw.text((160, y), line, fill=(255, 255, 255), font=font)
        y += 60
    return img


def render_editorial_image_scene(
    package,
    fps: int,
    global_start_time: float,
    badge_label: str,
    badge_colour: tuple,
    body_text: str,
    image_offset: int = 0,
) -> Iterator[np.ndarray]:
    """Image/video-illustrated editorial scene — real sourced imagery plus
    a readable text panel, rendered inside the studio's globe-screen
    content box so the newsroom (side menu, markets panel, floor) stays
    visible around it. Used in place of a charts/analytics scene:
    verification scores, source-tier mix, risk probabilities belong on
    the internal operator dashboard, never in the broadcast itself —
    viewers get pictures and facts, not pipeline telemetry."""
    from PIL import Image, ImageDraw

    from broadcast_os.config import CONFIG
    from broadcast_os.graphics._canvas import font_bold, font_regular, newsroom_bg, wrap_lines
    from broadcast_os.graphics.lower_third import LowerThirdGenerator

    story = package.story
    directive = package.directive
    duration = package.duration
    assets = package.assets

    cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
    content_w, content_h = cx1 - cx0, cy1 - cy0

    if assets:
        n = len(assets)
        off = image_offset % n
        rotated = assets[off:] + assets[:off]
        prepared = [cover_fit(Image.open(a.local_path).convert("RGB"), content_w, content_h) for a in rotated]
    else:
        prepared = [cover_fit(editorial_fallback_frame(story.headline), content_w, content_h)]

    n_images = len(prepared)
    segment_dur = duration / n_images
    lower_third = LowerThirdGenerator(headline=story.headline, source="WNX24", location=story.location)

    panel_h = 190
    panel_y0 = cy1 - panel_h

    n_frames = max(1, round(duration * fps))

    for i in range(n_frames):
        t = i / fps
        progress = i / max(1, n_frames - 1)
        seg_idx = min(n_images - 1, int(t // segment_dur))
        # Camera motion (Ken Burns zoom/pan) applies to the inset imagery
        # only, then it's composited into the studio's content box — the
        # studio itself never moves or gets replaced by a flat image.
        inset = apply_camera_motion(
            prepared[seg_idx].copy(), directive.camera_motion, directive.motion_speed, progress,
        )

        frame_img = newsroom_bg().copy()
        frame_img.paste(inset, (cx0, cy0))

        panel = Image.new("RGBA", frame_img.size, (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(panel)
        pdraw.rectangle([cx0, panel_y0, cx1, cy1], fill=(4, 8, 20, 205))
        badge_font = font_bold(22)
        bbox = pdraw.textbbox((0, 0), badge_label, font=badge_font)
        badge_w = (bbox[2] - bbox[0]) + 32
        pdraw.rectangle([cx0 + 20, panel_y0 + 16, cx0 + 20 + badge_w, panel_y0 + 50], fill=(*badge_colour, 255))
        pdraw.text((cx0 + 36, panel_y0 + 22), badge_label, fill=(10, 12, 20, 255), font=badge_font)

        body_font = font_regular(20)
        lines = wrap_lines(body_text, width=58, max_lines=4)
        ty = panel_y0 + 62
        for line in lines:
            pdraw.text((cx0 + 20, ty), line, fill=(230, 235, 245, 255), font=body_font)
            ty += 28

        frame_img = Image.alpha_composite(frame_img.convert("RGBA"), panel).convert("RGB")

        overlay = frame_img.convert("RGBA")
        overlay.alpha_composite(lower_third.frame(t, duration))
        yield np.asarray(overlay.convert("RGB"), dtype=np.uint8)
