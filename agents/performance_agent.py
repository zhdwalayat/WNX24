"""agents/performance_agent.py — Performance Agent.

Deterministic API-call agent (no LLM reasoning involved) — per
https://agentfactory.panaversity.org/'s Gate 1a, pulling view/like/
comment counts from the YouTube Data API for videos already uploaded
and writing them to the database is "one exact step that is the same
every time," not agent work.

Gate 2 (once, or every week?): runs once daily on its own schedule, not
per broadcast — performance numbers are meaningless immediately after
upload, so this is a separate scheduled job (see orchestrator.py's
"performance_check" job), not a pipeline step like the other agents.

Gate 3 (what "finished" looks like):
  Works from  — recent broadcasts() rows with a real (non-mock) YouTube URL
  Want at end — view/like/comment counts written to the performance table
  Done when   — every eligible video in the lookback window has a row
"""
import re

from loguru import logger

from agents.uploader_agent import get_youtube_client
from core.database import get_broadcasts_for_stats, save_performance_stats

_VIDEO_ID_RE = re.compile(r"[?&]v=([\w-]{6,})")


def extract_video_id(youtube_url: str) -> str | None:
    m = _VIDEO_ID_RE.search(youtube_url or "")
    return m.group(1) if m else None


def fetch_video_stats(youtube, video_id: str) -> dict | None:
    """One deterministic API call — statistics for a single video_id."""
    try:
        response = youtube.videos().list(part="statistics", id=video_id).execute()
        items = response.get("items", [])
        if not items:
            logger.warning(f"No stats returned for video {video_id}")
            return None
        stats = items[0]["statistics"]
        return {
            "views":    int(stats.get("viewCount", 0)),
            "likes":    int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        }
    except Exception as e:
        logger.warning(f"Could not fetch stats for {video_id}: {e}")
        return None


def run_performance_agent(days: int = 7) -> int:
    """Main entry point. Pulls stats for every real upload in the last
    `days` days and writes them to the `performance` table. Returns the
    number of videos updated. Never raises — a missing token or a failed
    API call just means fewer rows get updated this run."""
    logger.info(f"Starting Performance Agent — last {days} days")

    youtube = get_youtube_client()
    if youtube is None:
        logger.warning("No usable YouTube token — skipping performance check")
        return 0

    broadcasts = get_broadcasts_for_stats(days=days)
    updated = 0
    for b in broadcasts:
        video_id = extract_video_id(b["youtube_url"])
        if not video_id:
            continue
        stats = fetch_video_stats(youtube, video_id)
        if stats is None:
            continue
        save_performance_stats(
            video_id = video_id,
            show_id  = b["show_id"],
            story_id = b["story_id"],
            views    = stats["views"],
            likes    = stats["likes"],
            comments = stats["comments"],
        )
        updated += 1

    logger.info(f"Performance Agent complete — {updated} videos updated")
    return updated
