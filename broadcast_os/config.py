"""broadcast_os/config.py — render configuration for the BroadcastOS video layer.

Font paths point at the fonts already bundled in assets/fonts/ (the spec's
/usr/share/fonts/... paths only exist on Debian-family Linux and would break
this Windows deployment entirely) — same convention as video_editor.py,
video_composer.py and thumbnail_agent.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"


class TransitionType(str, Enum):
    CROSS_DISSOLVE = "cross"
    ZOOM = "zoom"
    LIGHT_SWEEP = "sweep"
    DIGITAL_PUSH = "push"
    BLUR_FADE = "blur"


class SceneType(str, Enum):
    INTRO = "intro"
    HEADLINE = "headline"
    FOOTAGE = "footage"
    MAP = "map"
    DATA = "data"
    ANALYSIS = "analysis"
    SUMMARY = "summary"
    OUTRO = "outro"


@dataclass
class BroadcastConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 18
    preset: str = "fast"

    bg_path: str = str(ASSETS_DIR / "newsroom_bg.jpg")
    logo_path: str = str(ASSETS_DIR / "WNX24_logo_3_globe.png")
    icon_path: str = str(ASSETS_DIR / "WNX24_icon.png")  # sign-off logo, end of broadcast
    font_bold: str = str(ASSETS_DIR / "fonts" / "DejaVuSansMono-Bold.ttf")
    font_regular: str = str(ASSETS_DIR / "fonts" / "DejaVuSansMono.ttf")

    # Brand colours (RGB)
    NAVY: tuple = (4, 8, 20)
    ELECTRIC: tuple = (29, 140, 248)
    RED: tuple = (198, 20, 30)
    WHITE: tuple = (255, 255, 255)
    GOLD: tuple = (244, 197, 66)
    AMBER: tuple = (244, 180, 40)
    GREEN: tuple = (46, 204, 113)
    DARK: tuple = (2, 5, 12)

    # Content box within assets/newsroom_bg.jpg's centre "globe screen" —
    # keeps the left show-menu rail and right markets/AI-network/clock
    # panels of the studio background visible around scene content.
    content_x0: int = 300
    content_y0: int = 165
    content_x1: int = 1310
    content_y1: int = 650

    # Lower third / ticker geometry
    lower_third_h: int = 96
    ticker_h: int = 44

    scene_durations: dict = field(
        default_factory=lambda: {
            "intro": 5,
            "headline": 5,
            "footage": 8,
            "map": 8,
            "data": 8,
            "analysis": 8,
            "summary": 7,
            "outro": 2,
        }
    )
    transition_frames: int = 15

    @property
    def content_w(self) -> int:
        return self.content_x1 - self.content_x0

    @property
    def content_h(self) -> int:
        return self.content_y1 - self.content_y0

    @property
    def size(self) -> tuple:
        return (self.width, self.height)


CONFIG = BroadcastConfig()
