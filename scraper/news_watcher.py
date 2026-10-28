# scraper/news_watcher.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from pytz import timezone as pytz_timezone

from agents.trend_detection_agent import run_trend_detection, scrape_rss_feeds
from agents.verification_agent import run_verification
from core.database import get_stats, init_db, save_stories

# ── PKT timezone ───────────────────────────────────────────
PKT = pytz_timezone("Asia/Karachi")

# ── Tier 1 sources — fast breaking news scan ────────────────
BREAKING_SOURCES = {
    # ── Global Tier 1 ─────────────────────────────────────
    "Reuters":          "https://feeds.reuters.com/reuters/topNews",
    "AP News":          "https://feeds.apnews.com/rss/TopNews",
    "BBC News":         "https://feeds.bbci.co.uk/news/rss.xml",
    "Al Jazeera":       "https://www.aljazeera.com/xml/rss/all.xml",

    # ── Pakistan Breaking ──────────────────────────────────
    "Geo News":         "https://www.geo.tv/rss/1",
    "ARY News":         "https://arynews.tv/feed/",
    "Dawn Pakistan":    "https://www.dawn.com/feeds/home",
    "Samaa News":       "https://www.samaa.tv/feed/",
    "The News":         "https://www.thenews.com.pk/rss/1/1",
    "Express Tribune":  "https://tribune.com.pk/feed",
    "APP News":         "https://www.app.com.pk/feed/",
}

# ── Full ingestion run ──────────────────────────────────────
def ingest_once():
    logger.info(
        f"News Watcher — ingesting at "
        f"{datetime.now().strftime('%H:%M:%S PKT')}"
    )
    try:
        stories = run_trend_detection()
        logger.info(f"Scraped: {len(stories)} raw stories")
        verified = run_verification(stories)
        logger.info(f"Verified: {len(verified)} stories")
        saved = save_stories(verified)
        logger.info(f"Saved to DB: {saved} new stories")
        stats = get_stats()
        logger.info(
            f"DB status — "
            f"New: {stats['new']} | "
            f"Broadcast: {stats['broadcast']} | "
            f"Rejected: {stats['rejected']}"
        )
    except Exception as e:
        logger.exception(f"Ingestion failed: {e}")

# ── Fast breaking-news scan (Tier 1 sources only) ───────────
def ingest_breaking():
    """
    Fast 5-minute check of Tier 1 sources only.
    For breaking news detection.
    """
    logger.info("Breaking news scan...")
    try:
        stories  = scrape_rss_feeds(BREAKING_SOURCES)
        verified = run_verification(stories)
        saved    = save_stories(verified)
        logger.info(f"Breaking scan: {saved} new stories")
    except Exception as e:
        logger.exception(f"Breaking scan failed: {e}")

# ── Run continuously ─────────────────────────────────────────
def start_watcher():
    logger.info("━" * 50)
    logger.info("  WNX24 NEWS WATCHER — STARTING")
    logger.info("  Breaking scan: every 5 minutes (Tier 1 sources)")
    logger.info("  Full ingestion: every 15 minutes")
    logger.info("━" * 50)
    init_db()
    ingest_once()

    scheduler = BlockingScheduler(timezone=PKT)

    # Breaking news — fast scan, Tier 1 sources only
    scheduler.add_job(
        ingest_breaking,
        IntervalTrigger(minutes=5, timezone=PKT),
        id   = "breaking_ingest",
        name = "Breaking News Fast Scan",
    )

    # Full scan still every 15 minutes
    scheduler.add_job(
        ingest_once,
        IntervalTrigger(minutes=15, timezone=PKT),
        id   = "full_ingest",
        name = "Full News Ingestion",
    )

    logger.info("Watcher running — press Ctrl+C to stop")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("WNX24 News Watcher stopped.")
        scheduler.shutdown()

if __name__ == "__main__":
    start_watcher()
