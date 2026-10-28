"""broadcast_os/graphics/ticker.py — scrolling news ticker overlay."""
from __future__ import annotations

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import H, W, font_bold, font_regular

BAR_H = CONFIG.ticker_h
BAR_Y = H - BAR_H
SCROLL_SPEED_PX_S = 120.0


class TickerGenerator:
    """
    Layout:
    - y = HEIGHT - 44
    - Dark background full width
    - Red WNX24 badge left
    - White divider line
    - Headlines scroll right to left at 120px/sec
    - Text: last 12 hours from DB, top 20 headlines
    """

    def __init__(self, text: str | None = None):
        self.text = text or self.get_ticker_text()

    def get_ticker_text(self) -> str:
        from core.database import get_fresh_stories

        try:
            stories = get_fresh_stories(limit=20, max_age_hours=12)
        except Exception:
            stories = []
        if not stories:
            return "WNX24 — World News Express 24 ● World News. Always."
        headlines = [s.raw.title[:80] for s in stories]
        return "  ●  ".join(headlines)

    def frame(self, t: float) -> Image.Image:
        """t: seconds elapsed since the broadcast started (scroll position
        is continuous across the whole video, not per-scene). Returns an
        RGBA overlay (1920x1080)."""
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        draw.rectangle([0, BAR_Y, W, H], fill=(8, 10, 20, 235))
        draw.line([(0, BAR_Y), (W, BAR_Y)], fill=(255, 255, 255, 200), width=2)

        badge_font = font_bold(22)
        badge_w = 150
        draw.rectangle([0, BAR_Y, badge_w, H], fill=(*CONFIG.RED, 255))
        draw.text((16, BAR_Y + BAR_H // 2 - 13), "WNX24 LIVE", fill=(255, 255, 255, 255), font=badge_font)
        draw.line([(badge_w, BAR_Y), (badge_w, H)], fill=(255, 255, 255, 160), width=2)

        text_font = font_regular(22)
        padded = f"{self.text}          "
        bbox = draw.textbbox((0, 0), padded, font=text_font)
        text_w = max(1, bbox[2] - bbox[0])

        # Continuous right-to-left scroll, wraps seamlessly
        travel = (t * SCROLL_SPEED_PX_S) % (text_w + W)
        x = W - travel + badge_w
        y = BAR_Y + BAR_H // 2 - 13
        draw.text((x, y), padded, fill=(255, 255, 255, 255), font=text_font)
        draw.text((x + text_w + badge_w, y), padded, fill=(255, 255, 255, 255), font=text_font)

        return overlay
