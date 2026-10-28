# scraper/breaking_news_log.py
"""One-shot, all-sources breaking news log.

Scrapes every configured source worldwide in a single pass
(agents/trend_detection_agent.py — Google News, Hacker News, Reddit, and
~60 mainstream/regional RSS feeds spanning World, Economy, Conflict,
Science, Health, Tech, Pakistan, Asia, Middle East, American and Chinese
outlets), verifies/scores every story (agents/verification_agent.py), and
prints + saves one comprehensive log of all verified stories ranked by
importance (confidence score, then raw engagement score) — a news-desk
rundown, not a broadcast pipeline run.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Story titles/URLs can carry emoji or other non-cp1252 characters — the
# default Windows console codepage can't print them, so widen stdout to
# UTF-8 (falls back to replacing unprintable chars rather than crashing).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime

from loguru import logger

from agents.trend_detection_agent import run_trend_detection
from agents.verification_agent import run_verification
from config.settings import config
from core.database import init_db, save_stories
from core.models import VerifiedStory
from utils.helpers import ensure_dirs, timestamp_pkt


def _rank(stories: list[VerifiedStory]) -> list[VerifiedStory]:
    """Most important first: verification confidence, then raw engagement
    score as the tiebreaker — run_verification already sorts by confidence
    alone, so ties (e.g. two stories both scoring 1.0) need this second key."""
    return sorted(stories, key=lambda s: (s.confidence_score, s.raw.score), reverse=True)


def build_log_text(ranked: list[VerifiedStory]) -> str:
    lines = [
        "=" * 100,
        f"  WNX24 BREAKING NEWS LOG — {timestamp_pkt()}",
        f"  {len(ranked)} verified stories, ranked by importance (confidence, then engagement score)",
        "=" * 100,
        "",
    ]
    for i, s in enumerate(ranked, 1):
        cross = f" | confirmed by: {', '.join(s.cross_sources)}" if s.cross_sources else ""
        lines.append(
            f"{i:>3}. conf={s.confidence_score:.2f}  score={s.raw.score:>5.0f}  "
            f"[{s.raw.source_name}]{cross}"
        )
        lines.append(f"     {s.raw.title}")
        lines.append(f"     {s.raw.url}")
        lines.append("")
    return "\n".join(lines)


def build_log_json(ranked: list[VerifiedStory]) -> list[dict]:
    return [
        {
            "rank":             i + 1,
            "confidence_score": s.confidence_score,
            "engagement_score": s.raw.score,
            "title":            s.raw.title,
            "source":           s.raw.source_name,
            "url":              s.raw.url,
            "published_at":     s.raw.published_at.isoformat(),
            "cross_sources":    s.cross_sources,
        }
        for i, s in enumerate(ranked)
    ]


def run_once() -> list[VerifiedStory]:
    logger.info("Breaking News Log — scraping all configured sources worldwide...")
    raw_stories = run_trend_detection()
    logger.info(f"Scraped {len(raw_stories)} raw stories from all sources")

    verified = run_verification(raw_stories)
    ranked = _rank(verified)

    init_db()
    saved = save_stories(verified)
    logger.info(f"Saved {saved} new stories to DB")

    log_text = build_log_text(ranked)
    print(log_text)

    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = os.path.join(config.output_dir, "logs", f"breaking_news_log_{ts}.txt")
    json_path = os.path.join(config.output_dir, "logs", f"breaking_news_log_{ts}.json")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(log_text)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(build_log_json(ranked), f, indent=2, ensure_ascii=False)

    logger.info(f"Breaking News Log saved — {txt_path}")
    logger.info(f"Breaking News Log saved — {json_path}")
    return ranked


if __name__ == "__main__":
    run_once()
