"""broadcast_os/scenes/summary_scene.py — Scene 7: SUMMARY."""
from __future__ import annotations

import numpy as np

from broadcast_os.graphics.lower_third import LowerThirdGenerator
from broadcast_os.graphics.summary_card import SummaryCardGenerator
from broadcast_os.graphics.ticker import TickerGenerator
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import (
    render_frames,
    summary_points_from_package,
    ticker_text_from_package,
)


def render(package: ScenePackage, fps: int, global_start_time: float) -> list[np.ndarray]:
    story = package.story
    directive = package.directive

    points = summary_points_from_package(package) or [story.headline]
    summary_gen = SummaryCardGenerator(points)
    lower_third = LowerThirdGenerator(headline=story.headline, source="WNX24", location=story.location)
    ticker = TickerGenerator(text=ticker_text_from_package(package))

    def compose(t: float):
        base = summary_gen.frame(t)
        out = base.convert("RGBA")
        out.alpha_composite(lower_third.frame(t, package.duration))
        out.alpha_composite(ticker.frame(global_start_time + t))
        return out.convert("RGB")

    return render_frames(
        package.duration, fps, compose,
        motion=directive.camera_motion, speed=directive.motion_speed,
    )
