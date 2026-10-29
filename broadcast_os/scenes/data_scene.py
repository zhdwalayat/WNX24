"""broadcast_os/scenes/data_scene.py — Scene 5: IMPACT.

Illustrates the story's impact with real sourced imagery, not charts —
verification/source-tier analytics belong on the internal operator
dashboard (dashboard/app.py), never in the viewer-facing broadcast.
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
        badge_label="IMPACT", badge_colour=CONFIG.ELECTRIC,
        body_text=body, image_offset=1,
    )
