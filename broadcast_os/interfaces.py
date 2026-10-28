"""broadcast_os/interfaces.py — inter-agent contracts for the BroadcastOS
video pipeline. Every agent and render-engine stage communicates through
these Pydantic models only — no agent reaches into another agent's internals.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StoryBlueprint(BaseModel):
    """Output of StoryPlannerAgent."""
    title: str
    category: str
    location: str
    headline: str
    priority: str  # HIGH/MEDIUM/LOW
    breaking_level: int  # 0-100
    duration: float  # seconds — narrated scenes run as long as the real narration does
    scene_sequence: list[str]
    statistics: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    # 5W+impact facts for the on-screen headline card — viewers need what
    # happened, when, where, and why it matters, not a single bare hook line.
    what: str = ""
    when: str = ""
    impact: str = ""


class VisualDirective(BaseModel):
    """Output of VisualDirectorAgent — one per scene."""
    scene_index: int
    scene_type: str
    duration: float
    transition_in: str
    camera_motion: str  # slow_zoom/pan_left/static/slow_zoom_out/orbit/pull_back
    motion_speed: float = 0.02  # 0.01-0.05
    overlay_text: str = ""
    asset_hint: str = ""


class AssetResult(BaseModel):
    """Output of AssetDiscoveryAgent."""
    local_path: str
    source_url: str
    source_name: str
    licence: str
    confidence: float
    width: int
    height: int


class GraphicSpec(BaseModel):
    """Output of a motion-graphics generator."""
    graphic_type: str  # lower_third/ticker/map/chart
    data: dict[str, Any] = Field(default_factory=dict)
    duration: int = 0
    animated: bool = True


class FootageSegment(BaseModel):
    """One story's slice of a multi-story footage scene (e.g. the 'summary'
    bulletin's 10-story rundown) — its own imagery, headline and location,
    so the on-screen picture actually changes as the narration moves from
    story to story instead of staying frozen on the lead story throughout."""
    caption: str
    location: str = ""
    assets: list[AssetResult] = Field(default_factory=list)
    # Relative share of the scene's total on-screen time this story should
    # get, proportional to how much of the combined narration is actually
    # about it (see broadcast_os.orchestrator._story_weights) — keeps the
    # screen in sync with what the anchor is saying instead of splitting
    # time evenly regardless of how long each story's narration runs.
    weight: float = 1.0


class ScenePackage(BaseModel):
    """Input to a scene renderer — everything needed for one scene."""
    scene_type: str
    duration: float
    story: StoryBlueprint
    directive: VisualDirective
    assets: list[AssetResult] = Field(default_factory=list)
    graphics: list[GraphicSpec] = Field(default_factory=list)
    voice_text: str = ""
    voice_path: str = ""
    # Populated only for a multi-story footage scene (see FootageSegment);
    # empty for every single-story scene, which keeps using `assets` above.
    segments: list[FootageSegment] = Field(default_factory=list)


class TimelineEntry(BaseModel):
    """One entry in the final render timeline."""
    scene_index: int
    scene_type: str
    start_time: float
    end_time: float
    scene_path: str = ""
    transition_out: str = ""
    audio_path: str = ""


class BroadcastTimeline(BaseModel):
    """Complete timeline — input to RenderEngine."""
    story_id: str
    title: str
    total_dur: float
    entries: list[TimelineEntry] = Field(default_factory=list)
    ticker_text: str = ""
