"""broadcast_os/orchestrator.py — BroadcastOS main entry point.

Connects the Story Planner, Visual Director, Asset Discovery and Voice
agents to the deterministic scene renderers and RenderEngine. Called from
the existing orchestrator.py's run_broadcast() as the new primary video
pipeline, ahead of video.video_composer and agents.avatar_agent (see
integration notes at the bottom of the root orchestrator.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime

from loguru import logger

from broadcast_os.agents.asset_discovery import AssetDiscoveryAgent
from broadcast_os.agents.story_planner import StoryPlannerAgent, _detect_category, _detect_location
from broadcast_os.agents.visual_director import VisualDirectorAgent
from broadcast_os.config import CONFIG
from broadcast_os.graphics.summary_card import extract_points
from broadcast_os.graphics.ticker import TickerGenerator
from broadcast_os.interfaces import (
    BroadcastTimeline,
    FootageSegment,
    GraphicSpec,
    ScenePackage,
    TimelineEntry,
)
from broadcast_os.render_engine import RenderEngine, SceneRenderResult
from broadcast_os.scenes import SCENE_RENDERERS
from broadcast_os.voice_agent import VoiceAgent
from config.settings import config
from core.models import AvatarVideo, VerifiedStory, VideoScript, VideoStatus

# Which narration section (broadcast_os.voice_agent.SECTION_ORDER) voices
# each scene type — one section per scene, no sharing, so every narrated
# scene runs exactly as long as its own real narration does (never a fixed
# template length). Scenes not listed are brief, silent cutaways/bumpers
# (intro sting, map location graphic, outro card) with a fixed nominal
# length — real news content is what should never be artificially capped.
_SCENE_TO_SECTION = {
    "headline": "intro",
    "footage": "what_happened",
    "data": "why_it_matters",
    "analysis": "expert_insight",
    "summary": "sign_off",
}
_MIN_NARRATED_SCENE_SECS = 3.0


def _keywords_for_story(raw, max_keywords: int = 5) -> list[str]:
    """Same keyword-extraction shape as
    asset_discovery._keywords_from_blueprint, but for one story in a
    multi-story bulletin lineup, which never gets its own StoryBlueprint
    (only the bulletin's lead story does)."""
    category = _detect_category(f"{raw.title} {raw.summary}")
    location = _detect_location(raw.title, raw.summary)
    words: list[str] = []
    for w in [location, category, *raw.title.split()]:
        w = w.strip()
        if len(w) < 3:
            continue
        if w.lower() not in (k.lower() for k in words):
            words.append(w)
        if len(words) >= max_keywords:
            break
    return words or ["world news"]


def _story_weights(what_happened: str, stories: list[VerifiedStory]) -> list[float]:
    """Relative screen-time share per story, proportional to how much of
    the combined `what_happened` narration is actually about each one —
    every builder's WHAT_HAPPENED instructions require each story's
    paragraph to open with 'According to [source name],', so locating
    those attribution points in the generated text (in whatever order the
    model wrote them in — not necessarily lineup order) gives real
    per-story chunk boundaries instead of guessing. Falls back to equal
    weight for any story whose attribution can't be located, and to fully
    equal weight for everyone if fewer than two stories can be located at
    all (keeps the old even-split behaviour rather than producing
    nonsense weights from an unreliable partial match)."""
    text_lower = what_happened.lower()
    found: list[tuple[int, int]] = []  # (text position, story index)
    for i, s in enumerate(stories):
        name = (s.raw.source_name or "").strip().lower()
        if not name:
            continue
        pos = text_lower.find(f"according to {name}")
        if pos == -1:
            pos = text_lower.find(name)
        if pos != -1:
            found.append((pos, i))

    if len(found) < 2:
        return [1.0] * len(stories)

    found.sort()
    bounds = [p for p, _ in found] + [len(what_happened)]
    chunk_len: dict[int, int] = {}
    for k, (_, story_idx) in enumerate(found):
        chunk_len[story_idx] = max(1, bounds[k + 1] - bounds[k])

    default = sum(chunk_len.values()) / max(1, len(chunk_len))
    return [float(chunk_len.get(i, default)) for i in range(len(stories))]


class BroadcastOrchestrator:
    """Main BroadcastOS entry point — see module docstring."""

    def __init__(self):
        self.story_planner = StoryPlannerAgent()
        self.visual_director = VisualDirectorAgent()
        self.asset_discovery = AssetDiscoveryAgent()
        self.voice_agent = VoiceAgent()
        self.render_engine = RenderEngine()

    def _save_stats_for_dashboard(self, story_id: str, blueprint) -> None:
        """Story statistics (region/status, cross-source count, etc.) no
        longer render into the broadcast video itself — see
        story_planner._base_scene_sequence — so they're saved here instead
        for admin review in the Control Room dashboard (dashboard/app.py's
        /outputs endpoint reads output/metadata/{story_id}_stats.json)."""
        stats_dir = os.path.join(config.output_dir, "metadata")
        os.makedirs(stats_dir, exist_ok=True)
        stats_path = os.path.join(stats_dir, f"{story_id}_stats.json")
        try:
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump({
                    "category": blueprint.category,
                    "location": blueprint.location,
                    "priority": blueprint.priority,
                    "breaking_level": blueprint.breaking_level,
                    **blueprint.statistics,
                }, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save dashboard stats for {story_id}: {e}")

    def _discover_segments(
        self, stories: list[VerifiedStory], story_id: str, what_happened: str = "",
    ) -> list[FootageSegment]:
        """One imagery+caption+location slice per story in a multi-story
        bulletin lineup, so the footage scene's on-screen picture (and
        lower third) actually changes story-by-story as the narration
        moves through the bulletin, instead of staying frozen on the
        lead story's imagery for the entire combined narration. Each
        segment's `weight` ties its on-screen time to how much of the
        real narration is actually about it (see _story_weights) so the
        screen stays in sync with the anchor instead of splitting time
        evenly regardless of how long each story's narration runs."""
        weights = _story_weights(what_happened, stories) if what_happened else [1.0] * len(stories)
        segments: list[FootageSegment] = []
        for i, s in enumerate(stories):
            raw = s.raw
            keywords = _keywords_for_story(raw)
            assets = self.asset_discovery.discover_for_keywords(
                keywords, f"{story_id}_{i}", count=2, fallback_caption=raw.title,
            )
            segments.append(FootageSegment(
                caption=raw.title,
                location=_detect_location(raw.title, raw.summary),
                assets=assets,
                weight=weights[i],
            ))
        return segments

    def produce_broadcast(
        self,
        story: VerifiedStory,
        script: VideoScript,
        show_id: str,
        stories: list[VerifiedStory] | None = None,
    ) -> AvatarVideo:
        story_id = script.story_id or story.raw.id or uuid.uuid4().hex[:12]
        logger.info(f"BroadcastOS — producing broadcast for story {story_id} ({show_id})")

        # 1. Plan
        blueprint = self.story_planner.plan(story, script)
        self._save_stats_for_dashboard(story_id, blueprint)

        # 2. Direct
        directives = self.visual_director.direct(blueprint)

        # 3. Discover assets — only needed if a footage scene is scheduled.
        # A multi-story lineup (e.g. the 'summary' bulletin) gets one
        # segment per story instead of one asset set for the lead story
        # alone — see _discover_segments / broadcast_os/scenes/footage_scene.py.
        assets = []
        footage_segments: list[FootageSegment] = []
        if "footage" in blueprint.scene_sequence:
            if stories and len(stories) > 1:
                footage_segments = self._discover_segments(stories, story_id, script.what_happened)
            else:
                assets = self.asset_discovery.discover(blueprint, count=5)

        # 4. Narration audio (async TTS agent, run to completion here)
        sections = asyncio.run(self.voice_agent.generate_all(script, story_id))

        # Shared, broadcast-wide graphics context (computed once, reused by
        # every scene that needs it, instead of every scene re-querying).
        ticker_text = TickerGenerator().get_ticker_text()
        summary_points = extract_points(script.what_happened, script.why_it_matters, script.expert_insight)

        scene_results: list[SceneRenderResult] = []
        timeline_entries: list[TimelineEntry] = []
        cursor = 0.0
        global_t = 0.0

        for directive in directives:
            scene_type = directive.scene_type
            section_key = _SCENE_TO_SECTION.get(scene_type)
            audio_path = sections.get(section_key, "") if section_key else ""
            voice_text = ""
            if section_key:
                voice_text = {
                    "intro": script.hook, "what_happened": script.what_happened,
                    "why_it_matters": script.why_it_matters,
                    "expert_insight": script.expert_insight, "sign_off": script.cta,
                }.get(section_key, "")

            # Narrated scenes run exactly as long as their real narration
            # does — never a fixed template length. Only non-narrated
            # bumpers/cutaways (intro, map, outro) keep the nominal
            # CONFIG.scene_durations length from the Visual Director.
            if section_key and audio_path:
                real_duration = self.voice_agent.duration(audio_path)
                scene_duration = max(_MIN_NARRATED_SCENE_SECS, real_duration) if real_duration > 0 else directive.duration
            else:
                scene_duration = directive.duration

            graphics: list[GraphicSpec] = [GraphicSpec(graphic_type="ticker", data={"text": ticker_text})]
            if scene_type == "summary":
                graphics.append(GraphicSpec(graphic_type="summary_points", data={"points": summary_points}))

            package = ScenePackage(
                scene_type=scene_type,
                duration=scene_duration,
                story=blueprint,
                directive=directive,
                assets=assets if scene_type == "footage" else [],
                graphics=graphics,
                voice_text=voice_text,
                voice_path=audio_path,
                segments=footage_segments if scene_type == "footage" else [],
            )

            renderer = SCENE_RENDERERS.get(scene_type)
            if renderer is None:
                logger.warning(f"BroadcastOS: no renderer for scene type '{scene_type}' — skipping")
                continue

            # `frames` is a generator, not a materialised list — duration
            # is already known (it's what told the renderer how many
            # frames to produce), so there's no need to consume the
            # generator just to count it.
            frames = renderer(package, CONFIG.fps, global_t)
            duration = scene_duration

            scene_results.append(SceneRenderResult(
                scene_index=directive.scene_index,
                scene_type=scene_type,
                frames=frames,
                duration=duration,
                transition_in=directive.transition_in,
                audio_path=audio_path,
            ))

            timeline_entries.append(TimelineEntry(
                scene_index=directive.scene_index,
                scene_type=scene_type,
                start_time=cursor,
                end_time=cursor + duration,
                transition_out=directive.transition_in,
                audio_path=audio_path,
            ))
            cursor += duration
            global_t += duration

        if not scene_results:
            logger.error("BroadcastOS: no scenes rendered — aborting")
            return AvatarVideo(script=script, status=VideoStatus.FAILED)

        timeline = BroadcastTimeline(
            story_id=story_id,
            title=blueprint.headline,
            total_dur=cursor,
            entries=timeline_entries,
            ticker_text=ticker_text,
        )

        out_dir = os.path.join(config.output_dir, "videos")
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(out_dir, f"{show_id}_{story_id}_broadcastos_{timestamp}.mp4")

        try:
            self.render_engine.render(timeline, scene_results, output_path)
        except Exception as e:
            logger.error(f"BroadcastOS render failed: {e}")
            return AvatarVideo(script=script, status=VideoStatus.FAILED)

        logger.info(f"BroadcastOS complete — {output_path} (~{timeline.total_dur:.1f}s)")
        return AvatarVideo(
            script=script,
            local_path=output_path,
            duration_secs=timeline.total_dur,
            status=VideoStatus.RENDERED,
        )


def run_broadcast_os(
    story: VerifiedStory, script: VideoScript, show_id: str, stories: list[VerifiedStory] | None = None,
) -> AvatarVideo:
    return BroadcastOrchestrator().produce_broadcast(story, script, show_id, stories=stories)
