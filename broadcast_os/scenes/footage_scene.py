"""broadcast_os/scenes/footage_scene.py — Scene 3: FOOTAGE.

Story imagery plays inside the studio's globe-screen content box, with the
newsroom (side menu, markets panel, floor) staying visible around it —
the broadcast never cuts away to a flat full-screen image; it always
reads as the anchor's screen changing within the same studio.
"""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import (
    font_bold,
    font_regular,
    newsroom_bg,
    wrap_lines,
)
from broadcast_os.graphics.lower_third import LowerThirdGenerator
from broadcast_os.graphics.ticker import TickerGenerator
from broadcast_os.graphics.transitions import TransitionEngine
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import (
    apply_camera_motion,
    cover_fit,
    editorial_fallback_frame,
    ticker_text_from_package,
)

CROSSFADE_WINDOW = 0.6


def _draw_location_tag(draw: ImageDraw.ImageDraw, location: str, cx1: int, cy0: int) -> None:
    if not location or location == "Global":
        return
    tag = location.upper()
    font = font_bold(22)
    bbox = draw.textbbox((0, 0), tag, font=font)
    tag_w = (bbox[2] - bbox[0]) + 28
    x1, y0 = cx1 - 16, cy0 + 16
    x0 = x1 - tag_w
    draw.rectangle([x0, y0, x1, y0 + 34], fill=(4, 8, 20))
    draw.rectangle([x0, y0, x0 + 4, y0 + 34], fill=(198, 20, 30))
    draw.text((x0 + 14, y0 + 6), tag, fill=(255, 255, 255), font=font)


def _draw_caption(draw: ImageDraw.ImageDraw, caption: str, cx0: int, cy1: int) -> None:
    if not caption:
        return
    font = font_regular(19)
    lines = wrap_lines(caption, width=64, max_lines=1)
    cap_y = cy1 - 40
    bbox = draw.textbbox((0, 0), lines[0], font=font)
    pad = 12
    draw.rectangle(
        [cx0 + 12 - pad, cap_y - 6, cx0 + 12 + (bbox[2] - bbox[0]) + pad, cap_y + 26],
        fill=(4, 8, 20, 210),
    )
    draw.text((cx0 + 12, cap_y), lines[0], fill=(230, 235, 245), font=font)


def render(package: ScenePackage, fps: int, global_start_time: float) -> Iterator[np.ndarray]:
    story = package.story
    directive = package.directive
    duration = package.duration

    cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
    content_w, content_h = cx1 - cx0, cy1 - cy0

    # A multi-story bulletin (package.segments — see FootageSegment) gets
    # one image group per story, each carrying its own caption/location and
    # a `weight` proportional to how much of the combined narration is
    # actually about it — so both the picture AND the time spent on it
    # track what the anchor is saying, instead of splitting screen time
    # evenly regardless of how long each story's narration runs. A normal
    # single-story scene (package.segments empty) behaves exactly as before
    # (a single implicit story with weight 1.0).
    prepared: list[Image.Image] = []
    img_story_idx: list[int] = []
    story_captions: list[str] = []
    story_locations: list[str] = []
    story_weights: list[float] = []

    if package.segments:
        for story_idx, seg in enumerate(package.segments):
            story_captions.append(seg.caption)
            story_locations.append(seg.location)
            story_weights.append(seg.weight if seg.weight and seg.weight > 0 else 1.0)
            seg_assets = seg.assets or []
            if seg_assets:
                for a in seg_assets:
                    prepared.append(cover_fit(Image.open(a.local_path).convert("RGB"), content_w, content_h))
                    img_story_idx.append(story_idx)
            else:
                prepared.append(cover_fit(editorial_fallback_frame(seg.caption or story.headline), content_w, content_h))
                img_story_idx.append(story_idx)
    else:
        story_captions = [story.headline]
        story_locations = [story.location]
        story_weights = [1.0]
        if package.assets:
            for a in package.assets:
                prepared.append(cover_fit(Image.open(a.local_path).convert("RGB"), content_w, content_h))
                img_story_idx.append(0)
        else:
            prepared.append(cover_fit(editorial_fallback_frame(story.headline), content_w, content_h))
            img_story_idx.append(0)

    n_images = len(prepared)
    n_stories = len(story_captions)

    # Per-story duration proportional to story_weights, then each story's
    # own images split that story's duration evenly among themselves.
    total_weight = sum(story_weights) or float(n_stories) or 1.0
    story_durations = [duration * (w / total_weight) for w in story_weights]

    image_durations: list[float] = []
    image_starts: list[float] = []
    story_bounds: list[tuple[float, float]] = []
    cum = 0.0
    for si in range(n_stories):
        s_dur = story_durations[si]
        story_bounds.append((cum, s_dur))
        count = img_story_idx.count(si)
        per_img = s_dur / count if count else s_dur
        for _ in range(count):
            image_starts.append(cum)
            image_durations.append(per_img)
            cum += per_img

    ticker = TickerGenerator(text=ticker_text_from_package(package))
    default_caption = package.voice_text[:130] if package.voice_text else story.headline

    n_frames = max(1, round(duration * fps))
    seg_idx = 0

    for i in range(n_frames):
        t = i / fps
        progress = i / max(1, n_frames - 1)

        # t is monotonically increasing across this loop, so a running
        # pointer is enough — no need to search from scratch each frame.
        while seg_idx < n_images - 1 and t >= image_starts[seg_idx] + image_durations[seg_idx]:
            seg_idx += 1
        seg_local_t = t - image_starts[seg_idx]
        cur_img_dur = image_durations[seg_idx]

        story_idx = img_story_idx[seg_idx]
        story_start, story_dur = story_bounds[story_idx]
        caption = (story_captions[story_idx] or default_caption)[:130]
        location = story_locations[story_idx]

        base = prepared[seg_idx]
        if n_images > 1 and seg_idx < n_images - 1 and seg_local_t > cur_img_dur - CROSSFADE_WINDOW:
            blend_t = max(0.0, min(1.0, (seg_local_t - (cur_img_dur - CROSSFADE_WINDOW)) / CROSSFADE_WINDOW))
            frame_arr = TransitionEngine.cross_dissolve(
                np.asarray(base, dtype=np.uint8), np.asarray(prepared[seg_idx + 1], dtype=np.uint8), blend_t,
            )
            inset = Image.fromarray(frame_arr)
        else:
            inset = base.copy()

        # Camera motion (Ken Burns) applies to the inset imagery only, then
        # it's composited into the studio's content box — the studio itself
        # never moves or gets replaced.
        inset = apply_camera_motion(inset, directive.camera_motion, directive.motion_speed, progress)

        frame_img = newsroom_bg().copy()
        frame_img.paste(inset, (cx0, cy0))

        draw = ImageDraw.Draw(frame_img)
        _draw_location_tag(draw, location, cx1, cy0)
        _draw_caption(draw, caption, cx0, cy1)

        lower_third = LowerThirdGenerator(headline=caption, source="WNX24", location=location)

        overlay = frame_img.convert("RGBA")
        overlay.alpha_composite(lower_third.frame(t - story_start, story_dur))
        overlay.alpha_composite(ticker.frame(global_start_time + t))
        yield np.asarray(overlay.convert("RGB"), dtype=np.uint8)
