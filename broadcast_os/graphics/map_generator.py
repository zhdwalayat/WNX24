"""broadcast_os/graphics/map_generator.py — animated map scene graphic."""
from __future__ import annotations

from PIL import Image, ImageDraw

from broadcast_os.config import CONFIG
from broadcast_os.graphics._canvas import (
    H,
    W,
    font_bold,
    newsroom_bg,
)

# ── Coarse equirectangular landmass blobs for a stylised dot-matrix
# world map (not geographically precise — a broadcast-graphic aesthetic,
# not a GIS product). (cx, cy, rx, ry) as fractions of the map box. ──
_LAND_BLOBS = [
    (0.16, 0.26, 0.11, 0.13),  # North America
    (0.20, 0.40, 0.05, 0.08),  # Central America
    (0.27, 0.63, 0.06, 0.17),  # South America
    (0.47, 0.20, 0.045, 0.075),  # Europe
    (0.44, 0.16, 0.02, 0.02),   # UK/Ireland
    (0.50, 0.53, 0.075, 0.19),  # Africa
    (0.58, 0.30, 0.06, 0.08),   # Middle East / Central Asia
    (0.66, 0.16, 0.15, 0.075),  # Russia / North Asia
    (0.68, 0.38, 0.10, 0.12),   # South & East Asia
    (0.80, 0.27, 0.02, 0.035),  # Japan
    (0.78, 0.63, 0.06, 0.06),   # Australia
]

# 55-country lat/lon gazetteer for locating the story/epicentre marker.
COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    "Pakistan": (30.4, 69.3), "India": (22.4, 78.7), "China": (35.9, 104.2),
    "Japan": (36.2, 138.3), "United States": (39.8, -98.6), "U.S.": (39.8, -98.6),
    "America": (39.8, -98.6), "Russia": (61.5, 105.3), "Ukraine": (48.4, 31.2),
    "Gaza": (31.5, 34.5), "Israel": (31.0, 34.8), "Iran": (32.4, 53.7),
    "Saudi Arabia": (23.9, 45.1), "UAE": (23.4, 53.8), "Dubai": (25.2, 55.3),
    "Qatar": (25.4, 51.2), "UK": (55.4, -3.4), "Britain": (55.4, -3.4),
    "France": (46.6, 2.2), "Germany": (51.2, 10.5), "Europe": (50.0, 10.0),
    "Afghanistan": (33.9, 67.7), "Bangladesh": (23.7, 90.4),
    "Sri Lanka": (7.9, 80.7), "Turkey": (38.9, 35.2), "Egypt": (26.8, 30.8),
    "Nigeria": (9.1, 8.7), "Kenya": (0.0, 37.9), "Brazil": (-14.2, -51.9),
    "Canada": (56.1, -106.3), "Mexico": (23.6, -102.5),
    "South Korea": (35.9, 127.8), "North Korea": (40.3, 127.5),
    "Taiwan": (23.7, 121.0), "Australia": (-25.3, 133.8),
    "Karachi": (24.9, 67.0), "Lahore": (31.5, 74.3), "Islamabad": (33.7, 73.1),
    "Indonesia": (-0.8, 113.9), "Philippines": (12.9, 121.8),
    "Vietnam": (14.1, 108.3), "Thailand": (15.9, 100.9),
    "South Africa": (-30.6, 22.9), "Global": (20.0, 10.0),
}


def _project(lat: float, lon: float, box_x0: float, box_y0: float, box_w: float, box_h: float) -> tuple:
    x = box_x0 + (lon + 180) / 360 * box_w
    y = box_y0 + (90 - lat) / 180 * box_h
    return x, y


class MapGenerator:
    """
    - Newsroom studio stays visible behind the map — only a light legibility
      wash over the map area itself, never a solid takeover of the content
      box (the old full opaque fill blacked out the whole studio view)
    - World map dot matrix (left ~62% of content box)
    - Country highlighted in gold/red
    - Epicentre marker with radar pulse (2s loop)
    - Connection lines from epicentre

    No numeric stats panel — region/status/magnitude-style figures are
    pipeline telemetry for the internal operator dashboard
    (dashboard/app.py) to review, never the viewer-facing broadcast. See
    broadcast_os.agents.story_planner._map_statistics for the figures
    themselves, which BroadcastOrchestrator persists for the dashboard.
    """

    def __init__(self, location: str):
        self.location = location
        self.marker_label = "STORY FOCUS"
        self.lat, self.lon = COUNTRY_COORDS.get(location, COUNTRY_COORDS["Global"])

    def frame(self, t: float) -> Image.Image:
        # Studio stays visible around and behind the map — never a flat
        # full-screen takeover. Only the map's own footprint gets a
        # translucent legibility wash (dots need contrast); the rest of
        # the content box shows the newsroom studio undimmed.
        img = newsroom_bg().copy()

        cx0, cy0, cx1, cy1 = CONFIG.content_x0, CONFIG.content_y0, CONFIG.content_x1, CONFIG.content_y1
        map_w = int((cx1 - cx0) * 0.62)
        map_x0, map_y0, map_x1, map_y1 = cx0, cy0, cx0 + map_w, cy1

        wash = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(wash).rectangle([map_x0, map_y0, map_x1, map_y1], fill=(6, 9, 18, 190))
        img = Image.alpha_composite(img.convert("RGBA"), wash).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Dot-matrix world map
        step = 13
        for gy in range(map_y0, map_y1, step):
            fy = (gy - map_y0) / max(1, map_y1 - map_y0)
            for gx in range(map_x0, map_x1, step):
                fx = (gx - map_x0) / max(1, map_x1 - map_x0)
                is_land = any(
                    ((fx - bcx) / brx) ** 2 + ((fy - bcy) / bry) ** 2 <= 1.0
                    for bcx, bcy, brx, bry in _LAND_BLOBS
                )
                colour = (60, 110, 200) if is_land else (25, 35, 60)
                draw.ellipse([gx - 1.3, gy - 1.3, gx + 1.3, gy + 1.3], fill=colour)

        # Highlighted country — soft gold glow, fades in over first second
        mx, my = _project(self.lat, self.lon, map_x0, map_y0, map_w, map_y1 - map_y0)
        glow_alpha = int(160 * min(1.0, t / 1.0))
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        for r, a in [(46, glow_alpha // 4), (30, glow_alpha // 2), (16, glow_alpha)]:
            gdraw.ellipse([mx - r, my - r, mx + r, my + r], fill=(*CONFIG.GOLD, a))
        img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Radar pulse rings — 2 second loop
        cycle = t % 2.0
        for phase in (0.0, 0.66, 1.33):
            pt = (cycle + phase) % 2.0 / 2.0
            r = 10 + pt * 55
            alpha = int(220 * (1 - pt))
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rdraw = ImageDraw.Draw(ring)
            rdraw.ellipse([mx - r, my - r, mx + r, my + r], outline=(*CONFIG.RED, alpha), width=3)
            img = Image.alpha_composite(img.convert("RGBA"), ring).convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.ellipse([mx - 6, my - 6, mx + 6, my + 6], fill=CONFIG.RED)

        # White label, not red-on-near-black — the red marker dot itself
        # already carries the colour cue; red text here would be low
        # contrast against the dark map.
        label_font = font_bold(20)
        draw.text((mx + 14, my - 12), self.marker_label, fill=CONFIG.WHITE, font=label_font)

        return img
