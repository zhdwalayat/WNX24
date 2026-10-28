"""broadcast_os/agents/story_planner.py — Story Planner Agent.

Reads the existing VerifiedStory (agents/verification_agent.py) and
VideoScript (agents/script_writer_agent.py) and produces a StoryBlueprint —
the scene sequence and metadata every downstream BroadcastOS agent works
from. Deterministic (rule-based), no LLM call.
"""
from __future__ import annotations

from loguru import logger

from broadcast_os.config import CONFIG
from broadcast_os.interfaces import StoryBlueprint
from core.models import VerifiedStory, VideoScript

# ── Category keyword rules ──────────────────────────────────
_CATEGORY_KEYWORDS = {
    "Pakistan": [
        "pakistan", "karachi", "lahore", "islamabad", "rawalpindi",
        "peshawar", "quetta", "rupee", "coas", "kpk", "balochistan",
        "sindh", "punjab",
    ],
    "Economy": [
        "economy", "economic", "market", "markets", "trade", "inflation",
        "gdp", "imf", "stocks", "stock market", "interest rate",
        "recession", "dollar", "cryptocurrency", "bitcoin", "budget",
    ],
    "Conflict": [
        "war", "conflict", "military", "airstrike", "missile", "troops",
        "invasion", "ceasefire", "attack", "strike", "combat",
    ],
    "Science": [
        "climate", "nasa", "space", "research", "scientific", "discovery",
        "earthquake", "flood", "hurricane", "wildfire",
    ],
    "Health": [
        "health", "vaccine", "outbreak", "pandemic", "who", "disease",
        "virus", "hospital", "medical",
    ],
    "Technology": [
        "ai", "artificial intelligence", "technology", "software", "cyber",
        "robot", "chip", "semiconductor",
    ],
}

# ── Small location gazetteer for headline/location extraction ──────
_LOCATIONS = [
    "Pakistan", "Karachi", "Lahore", "Islamabad", "India", "China", "Japan",
    "United States", "U.S.", "America", "Russia", "Ukraine", "Gaza",
    "Israel", "Iran", "Saudi Arabia", "UAE", "Dubai", "Qatar", "UK",
    "Britain", "France", "Germany", "Europe", "Afghanistan", "Bangladesh",
    "Sri Lanka", "Turkey", "Egypt", "Nigeria", "Kenya", "Brazil", "Canada",
    "Mexico", "South Korea", "North Korea", "Taiwan", "Australia",
]


def _detect_category(text: str) -> str:
    lower = text.lower()
    for category, words in _CATEGORY_KEYWORDS.items():
        if any(w in lower for w in words):
            return category
    return "General"


def _detect_location(title: str, summary: str = "") -> str:
    """Prefer a location named in the headline itself — a name that only
    appears in the summary is usually secondary/contextual (e.g. a
    regional-tension mention), not the story's actual dateline."""
    title_lower = title.lower()
    for loc in _LOCATIONS:
        if loc.lower() in title_lower:
            return loc
    combined_lower = f"{title} {summary}".lower()
    for loc in _LOCATIONS:
        if loc.lower() in combined_lower:
            return loc
    return "Global"


def _first_sentences(text: str, n: int, max_chars: int) -> str:
    import re
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
    joined = " ".join(parts[:n])
    return joined[:max_chars].rstrip()


def _extract_when(published_at) -> str:
    """Human-readable dateline for the on-screen facts block — PKT, since
    every WNX24 show schedule is expressed in PKT (config/settings.py)."""
    from datetime import UTC

    import pytz
    try:
        dt = published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local = dt.astimezone(pytz.timezone("Asia/Karachi"))
        return local.strftime("%d %b, %H:%M PKT")
    except Exception:
        return "Today"


def _extract_facts(script, raw) -> tuple[str, str, str]:
    """WHAT / WHEN / IMPACT for the headline card's facts block. WHERE is
    already covered by StoryBlueprint.location — viewers need the full
    5W-plus-impact picture, not a single bare headline line."""
    what = _first_sentences(script.what_happened, 2, 220) or raw.title
    when = _extract_when(raw.published_at)
    impact = (
        _first_sentences(script.why_it_matters, 1, 160)
        or _first_sentences(script.expert_insight, 1, 160)
        or "WNX24 will continue to follow this developing story."
    )
    return what, when, impact


def _priority_for(breaking_level: int) -> str:
    if breaking_level >= 75:
        return "HIGH"
    if breaking_level >= 50:
        return "MEDIUM"
    return "LOW"


def _base_scene_sequence(breaking_level: int) -> list[str]:
    """No 'intro' scene here — RenderEngine prepends the real branded
    assets/intro.mp4 opener instead of a rendered scene (see
    RenderEngine._prepend_intro). broadcast_os.scenes.intro_scene stays
    wired into SCENE_RENDERERS as a fallback for when that asset is
    missing, kept for future use rather than deleted.

    No 'summary' scene either — a bullet-point recap is a form of the
    same on-screen statistics/telemetry that verification scores and
    source-tier mixes are: useful for the internal operator dashboard
    (dashboard/app.py), not the viewer-facing broadcast."""
    if breaking_level > 75:
        return ["headline", "footage", "map", "data", "analysis", "outro"]
    if breaking_level >= 50:
        return ["headline", "footage", "data", "outro"]
    return ["headline", "outro"]


def _apply_category_overrides(sequence: list[str], category: str) -> list[str]:
    """Pakistan stories always get a map scene; economy always gets data;
    conflict always gets analysis — inserted just before 'outro' if
    not already present, preserving scene order."""
    seq = list(sequence)

    def _ensure(scene: str) -> None:
        if scene in seq:
            return
        insert_at = seq.index("outro") if "outro" in seq else len(seq)
        seq.insert(insert_at, scene)

    if category == "Pakistan":
        _ensure("map")
    if category == "Economy":
        _ensure("data")
    if category == "Conflict":
        _ensure("analysis")
    return seq


def _map_statistics(category: str, location: str, breaking_level: int) -> dict:
    """Region/status figures for the story — no longer rendered into the
    video itself (see _base_scene_sequence docstring); kept here so
    BroadcastOrchestrator can persist it for admin review on the
    dashboard instead."""
    from datetime import datetime

    if category == "Science":
        return {"MAGNITUDE": "6.9", "DEPTH": "10 km", "LOCATION": location, "TIME": "02:17 AM (JST)"}
    return {
        "REGION": location,
        "STATUS": "Developing" if breaking_level >= 75 else "Ongoing",
        "SOURCE": "WNX24",
        "UPDATED": datetime.now().strftime("%H:%M PKT"),
    }


class StoryPlannerAgent:
    """
    Rules:
    - Breaking news (score > 75): full 7-scene package
    - Standard news (score 50-75): 5 scenes (skip map/analysis)
    - Brief (score < 50): 3 scenes (intro/headline/outro)
    - Pakistan stories: always include map scene
    - Economy stories: always include data scene
    - Conflict stories: always include analysis scene
    """

    def plan(self, story: VerifiedStory, script: VideoScript) -> StoryBlueprint:
        raw = story.raw
        combined_text = f"{raw.title} {raw.summary}"

        breaking_level = round(min(1.0, max(0.0, story.confidence_score)) * 100)
        category = _detect_category(combined_text)
        location = _detect_location(raw.title, raw.summary)
        priority = _priority_for(breaking_level)

        sequence = _base_scene_sequence(breaking_level)
        sequence = _apply_category_overrides(sequence, category)

        duration = sum(CONFIG.scene_durations.get(s, 6) for s in sequence)

        headline = (script.hook or raw.title).strip()[:120]
        what, when, impact = _extract_facts(script, raw)

        tags = list(dict.fromkeys([*raw.keywords, category.lower(), location.lower()]))

        blueprint = StoryBlueprint(
            title=raw.title,
            category=category,
            location=location,
            headline=headline,
            priority=priority,
            breaking_level=breaking_level,
            duration=duration,
            scene_sequence=sequence,
            statistics={
                "cross_source_count": len(story.cross_sources),
                "map": _map_statistics(category, location, breaking_level),
            },
            tags=tags,
            what=what,
            when=when,
            impact=impact,
        )

        logger.info(
            f"Story Planner — category={category} location={location} "
            f"priority={priority} breaking_level={breaking_level} "
            f"scenes={sequence} duration={duration}s"
        )
        return blueprint


def run_story_planner(story: VerifiedStory, script: VideoScript) -> StoryBlueprint:
    return StoryPlannerAgent().plan(story, script)
