# orchestrator.py
import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Set, List
from pathlib import Path
from loguru import logger

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone as pytz_timezone

from config.settings import config
from core.database import (
    init_db, get_fresh_stories,
    mark_broadcast, save_broadcast,
    get_stats
)

# ── Broadcast history persistence ─────────────────────────
HISTORY_FILE = "output/metadata/broadcast_history.json"

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

# ── PKT timezone ───────────────────────────────────────────
PKT = pytz_timezone("Asia/Karachi")

# ── Regional source filter ─────────────────────────────────
def _get_sources_for_focus(focus: str) -> List[str]:
    source_map = {
        "asia":     config.content.asia_sources,
        "mideast":  config.content.mideast_sources,
        "affairs":  config.content.affairs_sources,
        "economy":  config.content.economy_sources,
        "science":  config.content.science_sources,
        "health":   config.content.health_sources,
        "conflict": config.content.conflict_sources,
    }
    return source_map.get(focus, [])

# ── Single broadcast ───────────────────────────────────────
def run_broadcast(show_id: str) -> bool:
    show = config.scheduler.shows.get(show_id)
    if not show:
        logger.error(f"Unknown show: {show_id}")
        return False

    logger.info("")
    logger.info("=" * 52)
    logger.info(f"  MNN — {show.name.upper()}")
    logger.info(f"  {datetime.now(PKT).strftime('%d %b %Y  |  %H:%M PKT')}")
    logger.info("=" * 52)

    try:
        # ── Step 1: Read from DB ───────────────────────────
        logger.info("[1/6] Reading stories from MNN database...")

        source_names = None
        if show.regional_focus != "global":
            source_names = _get_sources_for_focus(
                show.regional_focus
            )

        fresh = get_fresh_stories(
            limit          = 50,
            min_confidence = config.content.min_confidence_score,
            source_names   = source_names,
            max_age_hours  = max(1, show.lookback_minutes // 60),
        )

        # Filter already broadcast
        fresh = [
            s for s in fresh
            if s.raw.id not in broadcast_history
        ]

        # Fallback — DB empty, scrape live
        if not fresh:
            logger.warning(
                "DB empty or all stories broadcast — "
                "falling back to live scraping"
            )
            from agents.trend_detection_agent import run_trend_detection
            from agents.verification_agent import run_verification
            stories  = run_trend_detection()
            verified = run_verification(stories)
            fresh    = [
                s for s in verified
                if s.raw.id not in broadcast_history
            ]

        if not fresh:
            logger.warning("No fresh stories — skipping broadcast")
            return False

        stats = get_stats()
        logger.info(
            f"       {stats.get('verified', len(fresh))} stories in DB "
            f"| {len(fresh)} fresh | "
            f"Using top {min(len(fresh), show.story_count)}"
        )

        # Apply min score filter
        scored = [
            s for s in fresh
            if s.raw.score >= show.min_score
        ]
        if scored:
            fresh = scored

        top_stories = fresh[:show.story_count]
        logger.info(
            f"       Top: {top_stories[0].raw.title[:55]}"
        )

        # ── Step 2: Script ─────────────────────────────────
        logger.info("[2/6] Generating broadcast script...")
        from agents.script_writer_agent import run_script_writer
        script = run_script_writer(top_stories, show)
        logger.info(
            f"       {script.word_count} words "
            f"| ~{script.duration_secs}s"
        )

        # ── Step 3: Avatar ─────────────────────────────────
        logger.info("[3/6] Rendering avatar video...")
        from agents.avatar_agent import run_avatar_agent
        from core.models import VideoStatus
        video = run_avatar_agent(script)
        logger.info(f"       Status: {video.status}")

        if video.status == VideoStatus.FAILED:
            logger.error("Avatar generation failed — aborting")
            return False

        # ── Step 4: Thumbnail ──────────────────────────────
        logger.info("[4/6] Generating thumbnail...")
        from agents.thumbnail_agent import run_thumbnail_agent
        thumbnail = run_thumbnail_agent(
            top_stories[0], script, show_id
        )
        logger.info(f"       Saved: {thumbnail.image_path}")

        # ── Step 5: Upload ─────────────────────────────────
        logger.info("[5/6] Uploading to YouTube...")
        from agents.uploader_agent import run_uploader_agent
        from core.models import VideoPackage
        package = VideoPackage(
            story     = top_stories[0],
            script    = script,
            video     = video,
            thumbnail = thumbnail,
        )
        url              = run_uploader_agent(package, show)
        package.youtube_url = url

        # ── Step 6: Save to DB ─────────────────────────────
        logger.info("[6/6] Saving broadcast record...")
        for s in top_stories:
            broadcast_history.add(s.raw.id)
            mark_broadcast(s.raw.id, show_id)

        save_broadcast(
            show_id        = show_id,
            story_id       = top_stories[0].raw.id,
            script_text    = script.full_text,
            word_count     = script.word_count,
            youtube_url    = url or "",
            thumbnail_path = thumbnail.image_path,
        )
        _save_history()

        logger.info("")
        logger.info(f"  ✓ BROADCAST COMPLETE — {show.name}")
        logger.info(f"  Title:   {package.title or script.hook}")
        logger.info(f"  YouTube: {url or 'mock'}")
        logger.info("")
        return True

    except Exception as e:
        logger.exception(f"Broadcast failed [{show_id}]: {e}")
        return False

# ── Breaking news check ────────────────────────────────────
def check_breaking_news():
    show   = config.scheduler.shows["breaking"]
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=60)
    logger.info("Checking for breaking news...")

    try:
        fresh = get_fresh_stories(
            limit          = 20,
            min_confidence = config.content.min_confidence_score,
            max_age_hours  = 1,
        )

        breaking = [
            s for s in fresh
            if s.raw.score >= show.min_score
            and s.raw.id not in broadcast_history
            and (
                s.raw.published_at.replace(tzinfo=timezone.utc)
                if s.raw.published_at.tzinfo is None
                else s.raw.published_at
            ) >= cutoff
        ]

        if breaking:
            logger.info(
                f"Breaking story detected: "
                f"{breaking[0].raw.title[:55]}"
            )
            run_broadcast("breaking")
        else:
            logger.info("No breaking news — threshold not met")

    except Exception as e:
        logger.exception(f"Breaking news check failed: {e}")

# ── Setup APScheduler ──────────────────────────────────────
def setup_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=PKT)
    shows     = config.scheduler.shows

    for show_id, show in shows.items():
        if show.is_breaking or show_id == "week_review":
            continue

        for hour in show.schedule_pkt:
            if show_id == "conflict":
                # Mon-Sat only — skip Sunday
                for day in ["mon","tue","wed","thu","fri","sat"]:
                    scheduler.add_job(
                        run_broadcast,
                        CronTrigger(
                            day_of_week = day,
                            hour        = hour,
                            minute      = 0,
                            timezone    = PKT,
                        ),
                        kwargs    = {"show_id": show_id},
                        id        = f"{show_id}_{day}",
                        name      = f"{show.name} ({day})",
                        max_instances = 1,
                        coalesce  = True,
                    )
                logger.info(
                    f"Scheduled: {show.name:35s} "
                    f"{hour:02d}:00 PKT (Mon-Sat)"
                )
            else:
                scheduler.add_job(
                    run_broadcast,
                    CronTrigger(
                        hour     = hour,
                        minute   = 0,
                        timezone = PKT,
                    ),
                    kwargs        = {"show_id": show_id},
                    id            = show_id,
                    name          = show.name,
                    max_instances = 1,
                    coalesce      = True,
                )
                logger.info(
                    f"Scheduled: {show.name:35s} "
                    f"{hour:02d}:00 PKT (daily)"
                )

    # Week in Review — Sunday only
    scheduler.add_job(
        run_broadcast,
        CronTrigger(
            day_of_week = "sun",
            hour        = 20,
            minute      = 0,
            timezone    = PKT,
        ),
        kwargs        = {"show_id": "week_review"},
        id            = "week_review",
        name          = "MNN Week in Review",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info(
        f"Scheduled: MNN Week in Review              "
        f"20:00 PKT (Sunday)"
    )

    # Breaking news — every 30 minutes
    scheduler.add_job(
        check_breaking_news,
        IntervalTrigger(minutes=30, timezone=PKT),
        id            = "breaking_check",
        name          = "Breaking News Check",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info(
        "Scheduled: Breaking News Check              "
        "every 30 minutes"
    )

    return scheduler

# ── Entry points ───────────────────────────────────────────
def run_now(show_id: str = "breaking"):
    """Run a single broadcast immediately — for testing."""
    init_db()
    run_broadcast(show_id)

def start_24_7():
    """Start the full 24/7 APScheduler broadcast system."""
    init_db()

    logger.info("")
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║    MNN — MERIDIAN NEWS NETWORK — GOING LIVE  ║")
    logger.info("║         24/7 Broadcast Scheduler             ║")
    logger.info("╚══════════════════════════════════════════════╝")
    logger.info("")

    scheduler = setup_scheduler()

    logger.info("")
    logger.info(f"Total jobs scheduled: {len(scheduler.get_jobs())}")
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    logger.info("")

    # Run breaking check immediately on start
    check_breaking_news()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("MNN scheduler stopped.")
        scheduler.shutdown()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_now(sys.argv[1])
    else:
        start_24_7()
