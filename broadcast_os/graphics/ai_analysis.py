"""broadcast_os/graphics/ai_analysis.py — AI Analysis scene graphic."""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import H, W, ease_out, font_bold, font_regular

_SUBTEXT_BY_CATEGORY = {
    "Conflict": "ESCALATION RISK — NEXT 72 HOURS",
    "Science": "AFTERSHOCKS — NEXT 72 HOURS",
    "Economy": "MARKET VOLATILITY — NEXT 72 HOURS",
    "Health": "CASE TRAJECTORY — NEXT 72 HOURS",
    "Pakistan": "REGIONAL IMPACT — NEXT 72 HOURS",
}


def _risk_level(confidence: float) -> tuple[str, tuple]:
    if confidence > 0.9:
        return "HIGH PROBABILITY", CONFIG.RED
    if confidence > 0.7:
        return "MODERATE PROBABILITY", CONFIG.AMBER
    if confidence > 0.5:
        return "LOW PROBABILITY", CONFIG.GREEN
    return "UNCERTAIN", (150, 155, 165)


class AIAnalysisGenerator:
    """
    Left: neural network (glowing nodes, animated connections, slow pulse).
    Right: RISK ASSESSMENT panel — probability header, affected-areas
    legend, bottom AI-analysis statement.
    """

    def __init__(self, confidence: float, category: str, headline: str = ""):
        self.confidence = confidence
        self.category = category
        self.headline = headline
        self.level, self.level_colour = _risk_level(confidence)
        self.subtext = _SUBTEXT_BY_CATEGORY.get(category, "FURTHER DEVELOPMENTS — NEXT 72 HOURS")

        rng = random.Random(42)  # fixed seed — stable node layout across frames
        self._nodes = [
            (rng.uniform(0.08, 0.92), rng.uniform(0.12, 0.88)) for _ in range(16)
        ]
        # Connect each node to its ~3 nearest neighbours
        self._edges: list[tuple[int, int]] = []
        for i, (x1, y1) in enumerate(self._nodes):
            dists = sorted(
                range(len(self._nodes)),
                key=lambda j: (self._nodes[j][0] - x1) ** 2 + (self._nodes[j][1] - y1) ** 2,
            )
            for j in dists[1:4]:
                edge = (min(i, j), max(i, j))
                if edge not in self._edges:
                    self._edges.append(edge)

    def frame(self, t: float) -> Image.Image:
        img = Image.new("RGB", (W, H), CONFIG.DARK)
        draw = ImageDraw.Draw(img)
        cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
        draw.rectangle([cx0, cy0, cx1, cy1], fill=(6, 9, 18))

        net_w = int((cx1 - cx0) * 0.48)
        self._draw_network(draw, cx0, cy0, cx0 + net_w, cy1, t)
        self._draw_risk_panel(draw, cx0 + net_w + 30, cy0, cx1, cy1, t)
        return img

    def _draw_network(self, draw, x0, y0, x1, y1, t):
        w, h = x1 - x0, y1 - y0
        pts = [(x0 + fx * w, y0 + fy * h) for fx, fy in self._nodes]

        n_edges_shown = min(len(self._edges), int(len(self._edges) * min(1.0, t / 2.0)) + 1)
        for i, j in self._edges[:n_edges_shown]:
            draw.line([pts[i], pts[j]], fill=CONFIG.ELECTRIC, width=1)

        for idx, (px, py) in enumerate(pts):
            local_phase = (t + idx * 0.13) % 2.0
            local_pulse = (math.sin(local_phase * math.pi) + 1) / 2
            r = 4 + local_pulse * 3
            glow_r = r * 3
            for gr, ga in [(glow_r, 30), (glow_r * 0.6, 60)]:
                draw.ellipse([px - gr, py - gr, px + gr, py + gr], fill=None, outline=CONFIG.ELECTRIC)
            draw.ellipse([px - r, py - r, px + r, py + r], fill=CONFIG.ELECTRIC)

    def _draw_risk_panel(self, draw, x0, y0, x1, y1, t):
        header_font = font_bold(22)
        draw.text((x0, y0), "RISK ASSESSMENT", fill=(200, 210, 230), font=header_font)

        level_font = font_bold(40)
        draw.text((x0, y0 + 36), self.level, fill=self.level_colour, font=level_font)

        sub_font = font_regular(20)
        draw.text((x0, y0 + 86), self.subtext, fill=(190, 200, 220), font=sub_font)

        areas_font = font_bold(20)
        draw.text((x0, y0 + 140), "AFFECTED AREAS:", fill=(255, 255, 255), font=areas_font)

        legend = [
            ("High Risk", CONFIG.RED), ("Moderate Risk", CONFIG.AMBER), ("Low Risk", CONFIG.GREEN),
        ]
        ly = y0 + 176
        item_font = font_regular(19)
        slide = ease_out(min(1.0, t / 0.8))
        slide_offset = int((1.0 - slide) * 120)
        for name, colour in legend:
            lx = x0 + slide_offset
            draw.ellipse([lx + 2, ly + 4, lx + 16, ly + 18], fill=colour)
            draw.text((lx + 26, ly), name, fill=(220, 225, 235), font=item_font)
            ly += 32

        stmt_font = font_regular(18)
        statement = (
            f"AI models indicate a {self.level.lower()} of continued developments "
            f"in the coming days, based on cross-source verification and "
            f"historical pattern analysis for {self.category.lower()} events."
        )
        from broadcast_os.graphics._canvas import wrap_lines
        lines = wrap_lines(statement, width=48, max_lines=5)
        ly = y1 - 20 - len(lines) * 24
        for line in lines:
            draw.text((x0, ly), line, fill=(160, 170, 195), font=stmt_font)
            ly += 24
