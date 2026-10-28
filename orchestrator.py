# orchestrator.py
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from pytz import timezone as pytz_timezone

from config.settings import config
from core.database import (
    get_fresh_stories,
    get_stats,
    init_db,
    mark_broadcast,
    save_broadcast,
    save_stories,
)

# ── Broadcast history persistence ─────────────────────────
HISTORY_FILE = "output/metadata/broadcast_history.json"

def _load_history() -> set[str]:
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

broadcast_history: set[str] = _load_history()

# ── PKT timezone ───────────────────────────────────────────
PKT = pytz_timezone("Asia/Karachi")

# ── Regional source filter ─────────────────────────────────
def _get_sources_for_focus(focus: str) -> list[str]:
    source_map = {
        "asia":     config.content.asia_sources,
        "mideast":  config.content.mideast_sources,
        "affairs":  config.content.affairs_sources,
        "economy":  config.content.economy_sources,
        "science":  config.content.science_sources,
        "health":   config.content.health_sources,
        "conflict": config.content.conflict_sources,
        "pakistan": config.content.pakistan_sources,
    }
    return source_map.get(focus, [])

# ── Tiered minimum-score check ─────────────────────────────
def _passes_min_score(show, story) -> bool:
    """Apply show.min_score, with a stricter floor (min_score_tier4)
    for Tier 4 (eyewitness/social/user-submitted) sources if one is
    configured — see agents/verification_agent.py's five-tier source
    reliability model.

    For the breaking show specifically, a story also qualifies if it
    clears the real-world impact bar (compute_impact_score) even when its
    engagement score doesn't — engagement is a popularity proxy, not
    newsworthiness, and a significant story (mass casualties, a head of
    state acting, war, major policy consequence) can be breaking before
    it has picked up any online engagement at all."""
    threshold = show.min_score
    if show.min_score_tier4 is not None:
        from agents.verification_agent import TIER_4_SOURCES
        if story.raw.source_name in TIER_4_SOURCES:
            threshold = show.min_score_tier4

    if story.raw.score >= threshold:
        return True

    if show.is_breaking:
        from agents.verification_agent import compute_impact_score
        impact = compute_impact_score(story.raw)
        if impact >= config.content.min_impact_score:
            logger.debug(
                f"Breaking eligibility via impact score ({impact}) — "
                f"engagement score {story.raw.score} below threshold {threshold}: "
                f"{story.raw.title[:60]}"
            )
            return True

    return False

# ── Single broadcast ───────────────────────────────────────
def run_broadcast(show_id: str) -> bool:
    show = config.scheduler.shows.get(show_id)
    if not show:
        logger.error(f"Unknown show: {show_id}")
        return False

    logger.info("")
    logger.info("=" * 52)
    logger.info(f"  WNX24 — {show.name.upper()}")
    logger.info(f"  {datetime.now(PKT).strftime('%d %b %Y  |  %H:%M PKT')}")
    logger.info("=" * 52)

    try:
        # ── Step 1: Read from DB ───────────────────────────
        logger.info("[1/6] Reading stories from WNX24 database...")

        source_names = None
        if show.regional_focus != "global":
            source_names = _get_sources_for_focus(
                show.regional_focus
            )

        # A heavy-news-day story that dominates cross-source confirmation
        # (one event covered by 20+ outlets) can otherwise fill a small
        # limit entirely, before agents.planning_agent's diversity pass
        # ever sees a lower-scoring story from an underrepresented region
        # — 200 keeps effectively the whole fresh pool in play for that.
        fresh = get_fresh_stories(
            limit          = 200,
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
            from agents.news_api_agent import run_news_api_agent
            from agents.trend_detection_agent import run_trend_detection
            from agents.verification_agent import run_verification
            from core.models import VerificationStatus
            stories = run_trend_detection() + run_news_api_agent()
            verified = run_verification(stories)  # verified + pending, see docstring
            # Persist — without this, a story that later fails compliance
            # or gets marked rejected has no row to update, and the exact
            # same live-scraped top story keeps winning selection again
            # on every subsequent run.
            saved = save_stories(verified)
            logger.info(f"       Saved {saved} newly-scraped stories to DB")
            # This live-scrape path skips the DB round-trip (and with it
            # get_fresh_stories' lower 0.5-confidence floor for 'pending'
            # stories), so it requires full verification directly — a
            # 'pending' story from this pool has to wait for the DB-driven
            # path next run, same as it would if it had come from any
            # other scrape.
            fresh    = [
                s for s in verified
                if s.status == VerificationStatus.VERIFIED
                and s.raw.id not in broadcast_history
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

        # Apply min score filter (Tier 3 sources need a higher bar)
        scored = [
            s for s in fresh
            if _passes_min_score(show, s)
        ]
        if scored:
            fresh = scored

        if show.is_breaking or len(fresh) <= show.story_count:
            # Breaking is latency-critical (zero analysis, zero
            # judgment by design) — always take the deterministic
            # score-ordered slice, never the Planning Agent.
            top_stories = fresh[:show.story_count]
        else:
            from agents.planning_agent import run_planning_agent
            top_stories = run_planning_agent(fresh, show)
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

        # ── Editorial Review — house style lint, informational only ─
        from agents.style_agent import run_style_agent
        run_style_agent(script)

        # ── Legal / Ethics Review — PEMRA/defamation gate, mandatory
        # on every broadcast (this runs 24/7 unattended, so there's no
        # human editor to decide "if needed" — every script gets it) ─
        from agents.compliance_agent import run_compliance_agent
        compliance = run_compliance_agent(script, top_stories[0])
        if not compliance.passed:
            logger.error(
                f"Compliance check FAILED [{show_id}] — "
                f"risk={compliance.risk_level}: "
                f"{'; '.join(compliance.issues) or 'no details'} — "
                "aborting broadcast"
            )
            # Exclude these stories from future selection — otherwise the
            # same story/script combination keeps winning top-story
            # selection and re-failing compliance on every subsequent run
            # (get_fresh_stories only returns status='verified' stories).
            from core.database import mark_rejected
            for s in top_stories:
                mark_rejected(s.raw.id)
            return False

        # ── Step 3: Video ───────────────────────────────────
        logger.info("[3/6] Composing broadcast video...")
        from core.models import VideoStatus

        # BroadcastOS (broadcast_os/orchestrator.py) is the new primary
        # pipeline — agent-directed scene composition (story planner,
        # visual director, asset discovery, per-scene narration). Falls
        # back to video_composer, then avatar_agent, so a BroadcastOS
        # failure never blocks a broadcast (zero downtime).
        video = None
        try:
            from broadcast_os.orchestrator import BroadcastOrchestrator
            video = BroadcastOrchestrator().produce_broadcast(
                top_stories[0], script, show_id, stories=top_stories,
            )
            logger.info(f"       Pipeline: BroadcastOS | Status: {video.status}")
        except Exception as e:
            logger.warning(f"BroadcastOS failed: {e} — falling back to video_composer")
            video = None

        if video is None or video.status == VideoStatus.FAILED:
            from video.video_composer import run_video_composer
            video = run_video_composer(script, top_stories[0], show_id)
            logger.info(f"       Pipeline: video_composer (fallback) | Status: {video.status}")

        if video.status == VideoStatus.FAILED:
            logger.warning("video_composer failed — falling back to avatar_agent")
            from agents.avatar_agent import run_avatar_agent
            video = run_avatar_agent(script)
            logger.info(f"       Pipeline: avatar_agent (fallback) | Status: {video.status}")

        if video.status == VideoStatus.FAILED:
            logger.error("All video pipelines failed — aborting")
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
        url              = run_uploader_agent(package, show=show)
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
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    logger.info("Checking for breaking news...")

    try:
        fresh = get_fresh_stories(
            limit          = 20,
            min_confidence = config.content.min_confidence_score,
            max_age_hours  = 1,
        )

        breaking = [
            s for s in fresh
            if _passes_min_score(show, s)
            and s.raw.id not in broadcast_history
            and (
                s.raw.published_at.replace(tzinfo=UTC)
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

# ── YouTube video cleanup ───────────────────────────────────
def run_cleanup():
    """Delete old YouTube videos on schedule."""
    logger.info("Running YouTube video cleanup...")
    from agents.uploader_agent import cleanup_old_videos
    deleted = cleanup_old_videos(days_to_keep=3)
    logger.info(f"Cleanup done — {deleted} videos removed")

# ── Monthly database cleanup ────────────────────────────────
def run_db_cleanup():
    """Delete old story records on a monthly schedule."""
    logger.info("Running monthly database cleanup...")
    from core.database import cleanup_old_stories
    deleted = cleanup_old_stories(days=30)
    logger.info(f"DB cleanup done — {deleted} stories removed")

# ── Daily performance check ─────────────────────────────────
def run_performance_check():
    """Pull YouTube stats for the last week's real uploads."""
    logger.info("Running Performance Agent...")
    from agents.performance_agent import run_performance_agent
    updated = run_performance_agent(days=7)
    logger.info(f"Performance check done — {updated} videos updated")

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
                job_id = (show_id if len(show.schedule_pkt) == 1
                          else f"{show_id}_{hour}")
                scheduler.add_job(
                    run_broadcast,
                    CronTrigger(
                        hour     = hour,
                        minute   = 0,
                        timezone = PKT,
                    ),
                    kwargs        = {"show_id": show_id},
                    id            = job_id,
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
        name          = "WNX24 Week in Review",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info(
        "Scheduled: WNX24 Week in Review               "
        "20:00 PKT (Sunday)"
    )

    # Breaking news — every 5 minutes
    scheduler.add_job(
        check_breaking_news,
        IntervalTrigger(minutes=5, timezone=PKT),
        id            = "breaking_check",
        name          = "Breaking News Check",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info(
        "Scheduled: Breaking News Check              "
        "every 5 minutes"
    )

    # YouTube cleanup — daily during quiet hours
    scheduler.add_job(
        run_cleanup,
        CronTrigger(
            hour     = 3,
            minute   = 0,
            timezone = PKT,
        ),
        id            = "daily_cleanup",
        name          = "YouTube Video Cleanup",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info("Scheduled: YouTube Cleanup — 03:00 PKT daily")

    # Monthly DB cleanup — 1st of every month at 02:00 PKT
    scheduler.add_job(
        run_db_cleanup,
        CronTrigger(
            day      = 1,
            hour     = 2,
            minute   = 0,
            timezone = PKT,
        ),
        id            = "monthly_db_cleanup",
        name          = "Monthly DB Cleanup",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info("Scheduled: DB Cleanup — 1st of month 02:00 PKT")

    # Performance check — daily during quiet hours
    scheduler.add_job(
        run_performance_check,
        CronTrigger(
            hour     = 4,
            minute   = 0,
            timezone = PKT,
        ),
        id            = "performance_check",
        name          = "Performance Agent",
        max_instances = 1,
        coalesce      = True,
    )
    logger.info("Scheduled: Performance Check — 04:00 PKT daily")

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
    logger.info("║              WNX24 — GOING LIVE              ║")
    logger.info("║           24/7 Broadcast Scheduler           ║")
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
        logger.info("WNX24 scheduler stopped.")
        scheduler.shutdown()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_now(sys.argv[1])
    else:
        start_24_7()
