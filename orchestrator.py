# orchestrator.py
import os
import time
import json
import schedule
from pathlib import Path

HISTORY_FILE = "output/metadata/broadcast_history.json"
from datetime import datetime, timezone
from loguru import logger
from typing import Set

from agents.trend_detection_agent import run_trend_detection
from agents.verification_agent import run_verification
from agents.script_writer_agent import run_script_writer
from agents.avatar_agent import run_avatar_agent
from agents.thumbnail_agent import run_thumbnail_agent
from agents.uploader_agent import run_uploader_agent
from core.models import VideoPackage, VideoStatus
from config.settings import config

# ── Deduplication tracker ──────────────────────────────────
# Tracks story IDs already broadcast — prevents repeats
def _load_history() -> Set[str]:
    try:
        path = Path(HISTORY_FILE)
        if path.exists():
            data = json.loads(path.read_text())
            logger.info(f"Loaded {len(data)} stories from broadcast history")
            return set(data)
    except Exception as e:
        logger.warning(f"Could not load broadcast history: {e}")
    return set()

def _save_history() -> None:
    try:
        Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(HISTORY_FILE).write_text(
            json.dumps(list(broadcast_history), indent=2)
        )
    except Exception as e:
        logger.warning(f"Could not save broadcast history: {e}")

broadcast_history: Set[str] = _load_history()

# ── Single broadcast run ───────────────────────────────────
def run_broadcast(show_id: str) -> bool:
    show = config.scheduler.shows.get(show_id)
    if not show:
        logger.error(f"Unknown show: {show_id}")
        return False

    logger.info(f"")
    logger.info(f"{'='*50}")
    logger.info(f"  MNN — {show.name.upper()}")
    logger.info(f"  {datetime.now().strftime('%d %b %Y  |  %H:%M PKT')}")
    logger.info(f"{'='*50}")

    try:
        # ── Step 1: Trend Detection ────────────────────────
        logger.info(f"[1/6] Trend Detection...")
        stories = run_trend_detection()
        logger.info(f"      {len(stories)} stories scraped")

        # ── Step 2: Verification ───────────────────────────
        logger.info(f"[2/6] Verification...")
        verified = run_verification(stories)

        # Filter already broadcast stories
        fresh = [
            s for s in verified
            if s.raw.id not in broadcast_history
        ]

        if not fresh:
            logger.warning("No fresh verified stories -- skipping broadcast")
            return False

        logger.info(f"      {len(fresh)} fresh verified stories")

        # Filter by show's regional focus
        if show.regional_focus != "global":
            focused = _filter_regional(fresh, show.regional_focus)
            if focused:
                fresh = focused
                logger.info(
                    f"      {len(fresh)} after regional filter "
                    f"({show.regional_focus})"
                )

        # Filter by minimum score
        scored = [
            s for s in fresh
            if s.raw.score >= show.min_score
        ]
        if scored:
            fresh = scored

        # Take top N stories for this show
        top_stories = fresh[:show.story_count]

        logger.info(
            f"      Top story: "
            f"{top_stories[0].raw.title[:55]}"
        )

        # ── Step 3: Script Writer ──────────────────────────
        logger.info(f"[3/6] Script Writer...")
        script = run_script_writer(top_stories, show)
        logger.info(
            f"      {script.word_count} words / "
            f"{script.duration_secs}s"
        )

        # ── Step 4: Avatar Generator ───────────────────────
        logger.info(f"[4/6] Avatar Generator...")
        video = run_avatar_agent(script)
        logger.info(f"      Status: {video.status}")

        if video.status == VideoStatus.FAILED:
            logger.error("Avatar generation failed -- aborting")
            return False

        # ── Step 5: Thumbnail ──────────────────────────────
        logger.info(f"[5/6] Thumbnail...")
        thumbnail = run_thumbnail_agent(
            top_stories[0], script, show_id
        )
        logger.info(f"      Saved: {thumbnail.image_path}")

        # ── Step 6: Upload ─────────────────────────────────
        logger.info(f"[6/6] Uploading...")
        package = VideoPackage(
            story     = top_stories[0],
            script    = script,
            video     = video,
            thumbnail = thumbnail,
        )
        url              = run_uploader_agent(package)
        package.youtube_url = url

        if url:
            package.status = VideoStatus.UPLOADED
            # Mark stories as broadcast and persist
            for s in top_stories:
                broadcast_history.add(s.raw.id)
            _save_history()
            logger.info(f"Broadcast history saved — {len(broadcast_history)} total stories")

            logger.info(f"")
            logger.info(f"  BROADCAST COMPLETE")
            logger.info(f"  Show:    {show.name}")
            logger.info(f"  Title:   {package.title}")
            logger.info(f"  YouTube: {url}")
            logger.info(f"")
            return True

        else:
            logger.error("Upload failed")
            return False

    except Exception as e:
        logger.exception(f"Broadcast failed for {show_id}: {e}")
        return False


# ── Regional filter ────────────────────────────────────────
def _filter_regional(stories, focus: str):
    if focus == "asia":
        sources = config.content.asia_sources
    elif focus == "mideast":
        sources = config.content.mideast_sources
    elif focus == "cyber":
        sources = config.content.conflict_sources
    else:
        return stories


# ── Breaking news checker ──────────────────────────────────
def check_breaking_news():
    show = config.scheduler.shows["breaking"]
    logger.info("Checking for breaking news...")

    try:
        stories  = run_trend_detection()
        verified = run_verification(stories)

        # Only trigger if score exceeds breaking threshold
        breaking = [
            s for s in verified
            if s.raw.score >= show.min_score
            and s.raw.id not in broadcast_history
        ]

        if breaking:
            logger.info(
                f"Breaking story detected: "
                f"{breaking[0].raw.title[:55]}"
            )
            run_broadcast("breaking")
        else:
            logger.info("No breaking news detected")

    except Exception as e:
        logger.exception(f"Breaking news check failed: {e}")


# ── Schedule all shows ─────────────────────────────────────
def setup_schedule():
    shows = config.scheduler.shows

    # Scheduled shows — run at fixed PKT times
    for show_id, show in shows.items():
        if show.is_breaking:
            continue   # breaking handled separately

        for hour in show.schedule_pkt:
            time_str = f"{hour:02d}:00"
            schedule.every().day.at(time_str).do(
                run_broadcast, show_id=show_id
            )
            logger.info(
                f"Scheduled: {show.name:35s} "
                f"at {time_str} PKT"
            )

    # Breaking news — check every 30 minutes
    schedule.every(30).minutes.do(check_breaking_news)
    logger.info("Scheduled: Breaking News Check every 30 minutes")


# ── Main entry points ──────────────────────────────────────
def run_now(show_id: str = "breaking"):
    """Run a single broadcast immediately — for testing."""
    run_broadcast(show_id)


def start_24_7():
    """Start the full 24/7 broadcast scheduler."""
    logger.info("")
    logger.info("MNN — MERIDIAN NEWS NETWORK — GOING LIVE")
    logger.info("24/7 Broadcast Scheduler")
    logger.info("")

    setup_schedule()

    logger.info("")
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    logger.info("")

    # Run breaking news check immediately on start
    check_breaking_news()

    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)   # check every minute


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # Run specific show: python orchestrator.py morning
        run_now(sys.argv[1])
    else:
        # Start full 24/7 scheduler
        start_24_7()
