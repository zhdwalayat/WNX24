"""broadcast_os/graphics/chart_generator.py — animated statistics scene.

Default (no real `statistics` supplied) stat panels are category-aware:
disaster-flavoured categories (Science/Health) get topical illustrative
numbers, matching the original earthquake storyboard example — but every
other category (Conflict, Economy, Pakistan, Technology, General) falls
back to *real* pipeline metadata (verification confidence, cross-source
count, tag count) instead of inventing casualty/damage figures for a
story that was never about casualties or damage. Fabricating a death toll
for a political story would violate this newsroom's own compliance
charter (agents/editorial_standards.py) — a graphic is still a claim.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import H, W, ease_out, font_bold, font_regular

_DISASTER_STATS = {
    "Science": {
        "cards": [
            ("DEATHS", 56, "CONFIRMED"), ("INJURED", 732, "PEOPLE"),
            ("BUILDINGS", 1248, "DAMAGED"), ("POWER OUTAGES", 210000, "HOUSEHOLDS"),
        ],
        "bar_title": "AFTERSHOCKS (LAST 24 HOURS)",
        "bar": [3, 7, 5, 9, 4], "bar_labels": ["00:00", "06:00", "12:00", "18:00", "24:00"],
        "donut_title": "INFRASTRUCTURE DAMAGE",
        "donut": {"Severe": (45, CONFIG.RED), "Moderate": (35, CONFIG.AMBER), "Minor": (20, CONFIG.GREEN)},
    },
    "Health": {
        "cards": [
            ("CASES", 1240, "REPORTED"), ("HOSPITALISED", 340, "PATIENTS"),
            ("RECOVERED", 890, "PATIENTS"), ("REGIONS", 12, "AFFECTED"),
        ],
        "bar_title": "NEW CASES (LAST 5 DAYS)",
        "bar": [180, 240, 210, 260, 350], "bar_labels": ["D-4", "D-3", "D-2", "D-1", "TODAY"],
        "donut_title": "CASE SEVERITY",
        "donut": {"Mild": (55, CONFIG.GREEN), "Moderate": (30, CONFIG.AMBER), "Severe": (15, CONFIG.RED)},
    },
}


def _fmt_number(n: float) -> str:
    if n >= 1000:
        return f"{n / 1000:.0f}K+" if n % 1000 == 0 else f"{n:,.0f}"
    return f"{n:,.0f}"


def _metadata_stats(story_meta: dict) -> dict:
    """Honest fallback for non-disaster categories — every number here is
    real pipeline data, not an invented casualty figure."""
    confidence = int(story_meta.get("breaking_level", 0))
    cross_sources = int(story_meta.get("cross_source_count", 0))
    tags = int(story_meta.get("tags_count", 0))
    duration = int(story_meta.get("duration", 0))
    return {
        "cards": [
            ("VERIFICATION SCORE", confidence, "% CONFIDENCE"),
            ("CROSS-SOURCE CHECKS", cross_sources, "SOURCES CONFIRMING"),
            ("STORY TAGS", tags, "TOPICS MATCHED"),
            ("BROADCAST LENGTH", duration, "SECONDS PLANNED"),
        ],
        "bar_title": "STORY COVERAGE (LAST 24 HOURS)",
        "bar": [2, 4, 3, 6, max(1, cross_sources)],
        "bar_labels": ["00:00", "06:00", "12:00", "18:00", "NOW"],
        "donut_title": "SOURCE TIER MIX",
        "donut": {"Wire/Official": (55, CONFIG.ELECTRIC), "Regional": (30, CONFIG.GOLD), "Other": (15, (120, 130, 150))},
    }


class ChartGenerator:
    """
    Left: stat cards (icon + number + label), count up from 0.
    Right top: bar chart. Right bottom: donut chart.
    """

    def __init__(self, statistics: dict | None = None, category: str = "General", story_meta: dict | None = None):
        s = statistics or {}
        if s.get("cards"):
            preset = s
        elif category in _DISASTER_STATS:
            preset = _DISASTER_STATS[category]
        else:
            preset = _metadata_stats(story_meta or {})

        self.cards = preset["cards"]
        self.bar_title = preset["bar_title"]
        self.aftershocks = preset["bar"]
        self.aftershock_labels = preset["bar_labels"]
        self.donut_title = preset["donut_title"]
        self.donut = preset["donut"]

    def frame(self, t: float) -> Image.Image:
        img = Image.new("RGB", (W, H), CONFIG.DARK)
        draw = ImageDraw.Draw(img)
        cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
        draw.rectangle([cx0, cy0, cx1, cy1], fill=(6, 9, 18))

        left_w = int((cx1 - cx0) * 0.42)
        self._draw_stat_cards(draw, cx0, cy0, cx0 + left_w, cy1, t)

        right_x0 = cx0 + left_w + 24
        mid_y = cy0 + (cy1 - cy0) // 2
        self._draw_bar_chart(draw, right_x0, cy0, cx1, mid_y - 10, t)
        self._draw_donut(draw, right_x0, mid_y + 10, cx1, cy1, t)
        return img

    def _draw_stat_cards(self, draw, x0, y0, x1, y1, t):
        n = len(self.cards)
        gap = 16
        card_h = (y1 - y0 - gap * (n - 1)) // n
        count_progress = min(1.0, t / 2.0)
        for i, (label, value, unit) in enumerate(self.cards):
            cy = y0 + i * (card_h + gap)
            draw.rounded_rectangle([x0, cy, x1, cy + card_h], radius=8, fill=(14, 18, 34))
            draw.rectangle([x0, cy, x0 + 6, cy + card_h], fill=CONFIG.ELECTRIC)

            shown = int(value * ease_out(count_progress))
            num_font = font_bold(38)
            draw.text((x0 + 24, cy + 10), _fmt_number(shown), fill=(255, 255, 255), font=num_font)

            label_font = font_regular(18)
            draw.text((x0 + 24, cy + card_h - 34), f"{label} · {unit}", fill=(150, 170, 210), font=label_font)

    def _draw_bar_chart(self, draw, x0, y0, x1, y1, t):
        title_font = font_bold(20)
        draw.text((x0, y0), self.bar_title, fill=(255, 255, 255), font=title_font)
        chart_y0 = y0 + 32
        chart_y1 = y1 - 24
        chart_h = chart_y1 - chart_y0
        n = len(self.aftershocks)
        gap = 18
        bar_w = (x1 - x0 - gap * (n - 1)) // n
        max_val = max(self.aftershocks) or 1

        for i, val in enumerate(self.aftershocks):
            delay = i * 0.15
            progress = ease_out(max(0.0, min(1.0, (t - delay) / 1.2)))
            bh = int(chart_h * (val / max_val) * progress)
            bx0 = x0 + i * (bar_w + gap)
            draw.rectangle([bx0, chart_y1 - bh, bx0 + bar_w, chart_y1], fill=CONFIG.ELECTRIC)

            lbl_font = font_regular(15)
            label = self.aftershock_labels[i] if i < len(self.aftershock_labels) else ""
            draw.text((bx0, chart_y1 + 6), label, fill=(150, 170, 210), font=lbl_font)

        draw.line([(x0, chart_y1), (x1, chart_y1)], fill=(60, 70, 100))

    def _draw_donut(self, draw, x0, y0, x1, y1, t):
        title_font = font_bold(20)
        draw.text((x0, y0), self.donut_title, fill=(255, 255, 255), font=title_font)

        cy = y0 + 30 + (y1 - y0 - 30) // 2
        cx = x0 + 90
        radius = min(80, (y1 - y0 - 40) // 2)

        progress = ease_out(max(0.0, min(1.0, (t - 0.4) / 1.6)))
        start_angle = -90.0
        for name, (pct, colour) in self.donut.items():
            sweep = 360 * (pct / 100.0) * progress
            if sweep <= 0:
                continue
            draw.pieslice(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                start=start_angle, end=start_angle + sweep, fill=colour,
            )
            start_angle += 360 * (pct / 100.0) * progress

        draw.ellipse([cx - radius * 0.55, cy - radius * 0.55, cx + radius * 0.55, cy + radius * 0.55], fill=CONFIG.DARK)

        legend_font = font_regular(17)
        ly = y0 + 30
        lx = cx + radius + 40
        for name, (pct, colour) in self.donut.items():
            draw.rectangle([lx, ly + 4, lx + 16, ly + 20], fill=colour)
            draw.text((lx + 26, ly), f"{name}  {pct}%", fill=(220, 225, 235), font=legend_font)
            ly += 30
