"""broadcast_os/graphics/summary_card.py — summary scene overlay."""
from __future__ import annotations

import re

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import (
    H,
    W,
    ease_out,
    font_bold,
    font_regular,
    newsroom_bg,
    wrap_lines,
)


def extract_points(what_happened: str, why_it_matters: str, expert_insight: str) -> list[str]:
    """2 points from what_happened, 2 from why_it_matters, 1 from
    expert_insight — matching the spec's summary-bullet extraction rule."""

    def _sentences(text: str, n: int) -> list[str]:
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
        return parts[:n]

    points = (
        _sentences(what_happened, 2)
        + _sentences(why_it_matters, 2)
        + _sentences(expert_insight, 1)
    )
    return [p[:110] for p in points][:5] or ["WNX24 will continue to follow this story."]


class SummaryCardGenerator:
    """
    - Left overlay dark panel, red SUMMARY badge
    - Bullet checklist with checkmarks, each animates in sequentially
    """

    def __init__(self, points: list[str]):
        self.points = points or []

    def frame(self, t: float) -> Image.Image:
        base = newsroom_bg().copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
        panel_w = int((cx1 - cx0) * 0.75)
        draw.rectangle([cx0, cy0, cx0 + panel_w, cy1], fill=(4, 8, 20, 200))

        badge_font = font_bold(24)
        draw.rectangle([cx0 + 20, cy0 + 20, cx0 + 180, cy0 + 60], fill=(*CONFIG.RED, 255))
        draw.text((cx0 + 40, cy0 + 28), "SUMMARY", fill=(255, 255, 255, 255), font=badge_font)

        bullet_font = font_regular(24)
        check_font = font_bold(24)
        y = cy0 + 90
        per_reveal = 0.5
        for i, point in enumerate(self.points):
            reveal_t = i * per_reveal
            progress = ease_out(max(0.0, min(1.0, (t - reveal_t) / 0.4)))
            if progress <= 0:
                break
            slide = int((1.0 - progress) * 80)
            alpha = int(255 * progress)

            lines = wrap_lines(point, width=58, max_lines=2)
            draw.text((cx0 + 40 + slide, y), "✓", fill=(46, 204, 113, alpha), font=check_font)
            ty = y
            for line in lines:
                draw.text((cx0 + 74 + slide, ty), line, fill=(255, 255, 255, alpha), font=bullet_font)
                ty += 30
            y += max(36, len(lines) * 30 + 10)

        base.alpha_composite(overlay)
        return base.convert("RGB")
