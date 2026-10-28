"""broadcast_os/scenes/map_scene.py — Scene 4: MAP."""
from __future__ import annotations

import numpy as np

from broadcast_os.graphics.lower_third import LowerThirdGenerator
from broadcast_os.graphics.map_generator import MapGenerator
from broadcast_os.interfaces import ScenePackage
from broadcast_os.scenes._base import render_frames


def render(package: ScenePackage, fps: int, global_start_time: float) -> list[np.ndarray]:
    story = package.story
    directive = package.directive

    map_gen = MapGenerator(location=story.location)
    lower_third = LowerThirdGenerator(headline=story.headline, source="WNX24", location=story.location)

    def compose(t: float):
        base = map_gen.frame(t)
        out = base.convert("RGBA")
        out.alpha_composite(lower_third.frame(t, package.duration))
        return out.convert("RGB")

    return render_frames(
        package.duration, fps, compose,
        motion=directive.camera_motion, speed=directive.motion_speed,
    )
