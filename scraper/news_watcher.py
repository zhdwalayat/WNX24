# scraper/news_watcher.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import schedule
from loguru import logger
from datetime import datetime

from agents.trend_detection_agent import run_trend_detection
from agents.verification_agent import run_verification
from core.database import init_db, save_stories, get_stats

# ── Single ingestion run ───────────────────────────────────
def ingest_once():
    logger.info(
        f"News Watcher — ingesting at "
        f"{datetime.now().strftime('%H:%M:%S PKT')}"
    )
    try:
        # 1. Scrape all sources
        stories = run_trend_detection()
        logger.info(f"Scraped: {len(stories)} raw stories")

        # 2. Verify
        verified = run_verification(stories)
        logger.info(f"Verified: {len(verified)} stories")

        # 3. Save to DB — only new ones inserted
        saved = save_stories(verified)
        logger.info(f"Saved to DB: {saved} new stories")

        # 4. DB stats
        stats = get_stats()
        logger.info(
            f"DB status — "
            f"New: {stats['new']} | "
            f"Broadcast: {stats['broadcast']} | "
            f"Rejected: {stats['rejected']}"
        )

    except Exception as e:
        logger.exception(f"Ingestion failed: {e}")

# ── Run continuously ───────────────────────────────────────
def start_watcher(interval_minutes: int = 15):
    logger.info("━" * 50)
    logger.info("  MNN NEWS WATCHER — STARTING")
    logger.info(f"  Ingestion interval: every {interval_minutes} minutes")
    logger.info("━" * 50)

    # Init DB
    init_db()

    # Run immediately on start
    ingest_once()

    # Schedule every N minutes
    schedule.every(interval_minutes).minutes.do(ingest_once)

    logger.info(
        f"Watcher running — "
        f"next ingestion in {interval_minutes} minutes"
    )

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    start_watcher(interval_minutes=15)
