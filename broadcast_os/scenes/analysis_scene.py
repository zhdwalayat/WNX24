"""broadcast_os/scenes/analysis_scene.py — Scene 6: WHY IT MATTERS.

Illustrates context/expert insight with real sourced imagery, not an AI
risk-probability panel — that kind of pipeline telemetry belongs on the
internal operator dashboard (dashboard/app.py), never in the broadcast
viewers actually watch.
"""
from __future__ import annotations

import numpy as np

from broadcast_os.config import CONFIG
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import render_editorial_image_scene


def render(package: ScenePackage, fps: int, global_start_time: float) -> list[np.ndarray]:
    story = package.story
    body = package.voice_text or story.impact or story.headline

    return render_editorial_image_scene(
        package, fps, global_start_time,
        badge_label="WHY IT MATTERS", badge_colour=CONFIG.GOLD,
        body_text=body, image_offset=2,
    )
