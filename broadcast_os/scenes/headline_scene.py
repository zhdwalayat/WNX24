"""broadcast_os/scenes/headline_scene.py — Scene 2: HEADLINE."""
from __future__ import annotations

import numpy as np

from broadcast_os.graphics.headline_card import HeadlineCardGenerator
from broadcast_os.graphics.lower_third import LowerThirdGenerator
from broadcast_os.graphics.ticker import TickerGenerator
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import (
    composite_lower_third_and_ticker,
    render_frames,
    ticker_text_from_package,
)


def render(package: ScenePackage, fps: int, global_start_time: float) -> list[np.ndarray]:
    story = package.story
    directive = package.directive

    headline_gen = HeadlineCardGenerator(
        story.headline, is_breaking=story.priority == "HIGH", lead_in=story.what,
    )
    lower_third = LowerThirdGenerator(
        headline=story.headline, source="WNX24", location=story.location,
        is_breaking=story.priority == "HIGH",
    )
    ticker = TickerGenerator(text=ticker_text_from_package(package))

    def compose(t: float):
        base = headline_gen.frame(t)
        return composite_lower_third_and_ticker(
            base, lower_third, ticker, t, package.duration, global_start_time + t,
        )

    return render_frames(
        package.duration, fps, compose,
        motion=directive.camera_motion, speed=directive.motion_speed,
    )
