"""broadcast_os/agents/visual_director.py — Visual Director Agent.

The most important agent in the BroadcastOS pipeline: turns a StoryBlueprint
into one VisualDirective per scene, deciding camera motion, transitions and
motion intensity. Deterministic (rule-based), no LLM call.
"""
from __future__ import annotations

from loguru import logger

from broadcast_os.config import CONFIG
from broadcast_os.interfaces import StoryBlueprint, VisualDirective

# ── Camera motion per scene type ────────────────────────────
_CAMERA_MOTION = {
    "intro": "slow_zoom_in",
    "headline": "static_drift",
    "footage": "ken_burns_zoom",
    "map": "slow_orbit",
    "data": "static",
    "analysis": "slow_zoom_out",
    "summary": "static",
    "outro": "pull_back",
}

_BASE_SPEED = {
    "intro": 0.02,
    "headline": 0.01,
    "footage": 0.03,
    "map": 0.02,
    "data": 0.01,
    "analysis": 0.02,
    "summary": 0.01,
    "outro": 0.02,
}

# Transition applied when *entering* the scene that follows the named one —
# i.e. "after intro: zoom transition" means the headline scene's transition_in.
_TRANSITION_AFTER = {
    "intro": "zoom",
    "headline": "sweep",
    "footage": "cross",
    "map": "push",
    "data": "cross",
    "analysis": "zoom",
    "summary": "cross",
}

# Motion intensity tiers by story priority (HIGH=breaking, MEDIUM=standard, LOW=brief)
_INTENSITY_RANGE = {
    "HIGH": (0.04, 0.05),
    "MEDIUM": (0.02, 0.03),
    "LOW": (0.01, 0.02),
}

_OVERLAY_HINT = {
    "intro": "",
    "headline": "{headline}",
    "footage": "{location}",
    "map": "{location}",
    "data": "STATISTICS",
    "analysis": "AI ANALYSIS",
    "summary": "SUMMARY",
    "outro": "",
}


def _scaled_speed(scene_type: str, priority: str) -> float:
    base = _BASE_SPEED.get(scene_type, 0.02)
    lo, hi = _INTENSITY_RANGE.get(priority, _INTENSITY_RANGE["MEDIUM"])
    # Map the scene's relative base speed (0.01-0.03 authoring range) onto
    # this story's intensity tier, preserving each scene's relative motion.
    t = (base - 0.01) / (0.03 - 0.01)
    t = max(0.0, min(1.0, t))
    return round(lo + (hi - lo) * t, 4)


class VisualDirectorAgent:
    """
    Camera motion rules:
    - Intro:    slow zoom in (0.02 speed)
    - Headline: static with slight drift (0.01)
    - Footage:  Ken Burns zoom (0.03)
    - Map:      slow orbit (0.02)
    - Data:     static (0.01)
    - Analysis: slow zoom out (0.02)
    - Summary:  static (0.01)
    - Outro:    pull back (0.02)

    Transition rules — after X, transition Y into the next scene.
    Motion intensity scales with story priority: breaking > standard > brief.
    """

    def direct(self, blueprint: StoryBlueprint) -> list[VisualDirective]:
        directives: list[VisualDirective] = []
        sequence = blueprint.scene_sequence

        for i, scene_type in enumerate(sequence):
            duration = CONFIG.scene_durations.get(scene_type, 6)
            camera_motion = _CAMERA_MOTION.get(scene_type, "static")
            speed = _scaled_speed(scene_type, blueprint.priority)

            if i == 0:
                transition_in = "cross"
            else:
                prev_scene = sequence[i - 1]
                transition_in = _TRANSITION_AFTER.get(prev_scene, "cross")

            overlay_template = _OVERLAY_HINT.get(scene_type, "")
            overlay_text = overlay_template.format(
                headline=blueprint.headline, location=blueprint.location
            ) if overlay_template else ""

            asset_hint = f"{blueprint.category} {blueprint.location}".strip()

            directives.append(VisualDirective(
                scene_index=i,
                scene_type=scene_type,
                duration=duration,
                transition_in=transition_in,
                camera_motion=camera_motion,
                motion_speed=speed,
                overlay_text=overlay_text,
                asset_hint=asset_hint,
            ))

        logger.info(
            f"Visual Director — {len(directives)} directives, "
            f"priority={blueprint.priority}"
        )
        return directives


def run_visual_director(blueprint: StoryBlueprint) -> list[VisualDirective]:
    return VisualDirectorAgent().direct(blueprint)
