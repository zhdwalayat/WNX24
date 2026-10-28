"""broadcast_os/graphics/lower_third.py — animated lower third overlay."""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import (
    H,
    W,
    ease_out,
    font_bold,
    font_regular,
)

BAR_H = CONFIG.lower_third_h
BAR_Y = H - CONFIG.ticker_h - BAR_H  # sits directly above the ticker
SLIDE_SECS = 0.5


class LowerThirdGenerator:
    """
    Layout:
    - Dark navy bar bottom of frame (above the ticker)
    - Red left accent 6px
    - Headline text white bold
    - Source + location smaller
    - WNX24 badge right side red
    - LIVE dot pulsing
    - Slides in from left (0-0.5s), stays, slides out right (last 0.5s)
    """

    def __init__(self, headline: str, source: str, location: str = "", is_breaking: bool = False):
        self.headline = (headline or "")[:55]
        self.source = source or ""
        self.location = location or ""
        self.is_breaking = is_breaking

    def frame(self, t: float, duration: float) -> Image.Image:
        """t: seconds elapsed since this lower third appeared; duration: total
        seconds it is shown for. Returns an RGBA overlay (1920x1080)."""
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Slide in/out offset
        if t < SLIDE_SECS:
            offset = int((1.0 - ease_out(t / SLIDE_SECS)) * W * 0.4)
        elif t > duration - SLIDE_SECS:
            remain = max(0.0, duration - t)
            offset = int((1.0 - ease_out(remain / SLIDE_SECS)) * W * 0.4)
        else:
            offset = 0

        x0 = offset
        bar_colour = (10, 14, 28, 235)
        draw.rectangle([x0, BAR_Y, x0 + W, BAR_Y + BAR_H], fill=bar_colour)
        draw.rectangle([x0, BAR_Y, x0 + 6, BAR_Y + BAR_H], fill=(*CONFIG.RED, 255))

        headline_font = font_bold(30)
        draw.text((x0 + 30, BAR_Y + 14), self.headline, fill=(255, 255, 255, 255), font=headline_font)

        sub_font = font_regular(20)
        sub = " · ".join(s for s in [self.source, self.location] if s)
        draw.text((x0 + 30, BAR_Y + 54), sub, fill=(180, 190, 210, 255), font=sub_font)

        # WNX24 badge, right side
        badge_font = font_bold(24)
        badge_text = "WNX24"
        bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_w = (bbox[2] - bbox[0]) + 40
        badge_x1 = x0 + W - 20
        badge_x0 = badge_x1 - badge_w
        draw.rectangle([badge_x0, BAR_Y + 18, badge_x1, BAR_Y + 18 + 40], fill=(*CONFIG.RED, 255))
        draw.text((badge_x0 + 20, BAR_Y + 26), badge_text, fill=(255, 255, 255, 255), font=badge_font)

        if self.is_breaking:
            pulse = (math.sin(t * math.pi * 2) + 1) / 2  # 2s pulse cycle
            dot_r = 6 + pulse * 2
            dot_cx, dot_cy = badge_x0 - 26, BAR_Y + 38
            alpha = int(160 + pulse * 95)
            draw.ellipse(
                [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
                fill=(230, 50, 50, alpha),
            )

        return overlay
