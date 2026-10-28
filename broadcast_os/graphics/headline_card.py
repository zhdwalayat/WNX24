"""broadcast_os/graphics/headline_card.py — breaking headline scene overlay.

The headline card gives the headline plus one flowing lead sentence of
real context — written the way BBC/Al Jazeera title cards actually read,
not a form of labelled WHAT/WHEN/WHERE/IMPACT fields. The full detail
(what happened, why it matters) is delivered naturally through the
narrated scenes that follow, exactly like a real broadcast handing off
from the headline card to the reporter's copy.
"""
from __future__ import annotations

import math
from datetime import datetime

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import (
    H,
    W,
    ease_out,
    font_bold,
    font_regular,
    newsroom_bg,
    wnx24_logo,
    wrap_lines,
)
from config.settings import config as app_config


class HeadlineCardGenerator:
    """
    - Semi-dark overlay centre area
    - Red BREAKING NEWS banner top left, LIVE badge top right
    - Headline (1-2 lines)
    - One flowing lead-in sentence beneath it — plain prose, no labels
    - WNX24 logo + tagline, time, bottom row

    Animation: headline slides up (0-0.5s), banner fades in (0-0.3s),
    lead-in sentence fades in (0.6-1.2s).
    """

    def __init__(self, headline: str, is_breaking: bool = True, lead_in: str = ""):
        self.headline = headline or "Breaking News"
        self.is_breaking = is_breaking
        self.lead_in = lead_in
        self._tagline = app_config.channel.tagline.upper()

    def frame(self, t: float) -> Image.Image:
        base = newsroom_bg().copy()

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
        draw.rectangle([cx0, cy0, cx1, cy1], fill=(4, 8, 20, 175))

        # Banner — fades in 0-0.3s
        banner_alpha = int(255 * min(1.0, t / 0.3))
        banner_font = font_bold(28)
        banner_text = "BREAKING NEWS" if self.is_breaking else "HEADLINE"
        bbox = draw.textbbox((0, 0), banner_text, font=banner_font)
        banner_w = (bbox[2] - bbox[0]) + 40
        draw.rectangle([cx0 + 20, cy0 + 20, cx0 + 20 + banner_w, cy0 + 66], fill=(*CONFIG.RED, banner_alpha))
        draw.text((cx0 + 40, cy0 + 30), banner_text, fill=(255, 255, 255, banner_alpha), font=banner_font)

        # Headline — slides up from below over 0-0.5s
        head_font = font_bold(46)
        lines = wrap_lines(self.headline, width=30, max_lines=2)
        line_h = 58
        target_y = cy0 + 96
        rise = int((1.0 - ease_out(min(1.0, t / 0.5))) * 50)
        y = target_y + rise
        head_alpha = int(255 * min(1.0, t / 0.5 + 0.15))
        for line in lines:
            draw.text((cx0 + 40, y), line, fill=(255, 255, 255, head_alpha), font=head_font)
            y += line_h

        # One flowing lead-in sentence — plain prose, fades in 0.6-1.2s
        if self.lead_in:
            lead_alpha = int(255 * max(0.0, min(1.0, (t - 0.6) / 0.6)))
            lead_font = font_regular(24)
            lead_lines = wrap_lines(self.lead_in, width=64, max_lines=3)
            ly = y + 14
            for line in lead_lines:
                draw.text((cx0 + 40, ly), line, fill=(205, 210, 225, lead_alpha), font=lead_font)
                ly += 32

        # Tagline — types in 0.5-1.5s
        type_progress = max(0.0, min(1.0, (t - 0.5) / 1.0))
        n_chars = int(len(self._tagline) * type_progress)
        tag_font = font_regular(24)
        # Gold, not brand red — red text directly on a near-black panel
        # reads as low-contrast/muddy on screen; red stays reserved for
        # fills/badges where it sits behind white text instead.
        draw.text(
            (cx0 + 40, cy1 - 60), self._tagline[:n_chars],
            fill=(*CONFIG.GOLD, 255), font=tag_font,
        )

        # WNX24 logo, bottom-left of content box
        logo = wnx24_logo()
        if logo is not None:
            lg = logo.copy()
            lg.thumbnail((70, 70), Image.LANCZOS)
            overlay.paste(lg, (cx0 + 40, cy1 - 60 - lg.height - 8), lg)

        # Time — right side
        time_font = font_regular(24)
        time_text = datetime.now().strftime("%I:%M %p PKT")
        bbox = draw.textbbox((0, 0), time_text, font=time_font)
        draw.text((cx1 - (bbox[2] - bbox[0]) - 30, cy1 - 60), time_text, fill=(190, 195, 215, 255), font=time_font)

        # LIVE badge — top right, pulsing white dot
        live_font = font_bold(24)
        live_text = "LIVE"
        bbox = draw.textbbox((0, 0), live_text, font=live_font)
        badge_w = (bbox[2] - bbox[0]) + 60
        badge_x0 = cx1 - badge_w - 20
        draw.rectangle([badge_x0, cy0 + 20, badge_x0 + badge_w, cy0 + 66], fill=(*CONFIG.RED, 255))
        pulse = (math.sin(t * math.pi * 2) + 1) / 2
        dot_r = 5 + pulse * 2
        draw.ellipse(
            [badge_x0 + 18 - dot_r, cy0 + 43 - dot_r, badge_x0 + 18 + dot_r, cy0 + 43 + dot_r],
            fill=(255, 255, 255, 255),
        )
        draw.text((badge_x0 + 34, cy0 + 30), live_text, fill=(255, 255, 255, 255), font=live_font)

        base = base.convert("RGBA")
        base.alpha_composite(overlay)
        return base.convert("RGB")
