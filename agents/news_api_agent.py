"""agents/news_api_agent.py — NewsAPI.org integration.

Deterministic API-call agent (no LLM reasoning involved) — per
https://agentfactory.panaversity.org/'s agent-vs-code guidance, a fixed
"call this endpoint, parse this shape" task doesn't need an autonomous
agent, so this follows the same plain-function style as
trend_detection_agent.py rather than wrapping an LLM around it.
"""
from datetime import UTC, datetime

import httpx
from loguru import logger

from agents.trend_detection_agent import is_recent, is_relevant, make_id
from config.settings import config
from core.models import RawStory, SourceType

NEWSAPI_BASE = "https://newsapi.org/v2"

# NewsAPI category -> query keywords, so one call per category covers the
# same beats as the RSS scrapers (world, economy, conflict, tech, science).
_QUERIES = [
    "world news",
    "economy OR markets OR inflation",
    "war OR conflict OR geopolitics",
    "artificial intelligence OR technology",
    "science OR climate OR health",
]


def _parse_published(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except Exception:
        return datetime.now(UTC)


def fetch_everything(query: str, page_size: int = 20) -> list[RawStory]:
    """Call NewsAPI's /everything endpoint for a single query and return
    matching RawStory objects."""
    if not config.api.newsapi_key:
        return []

    try:
        resp = httpx.get(
            f"{NEWSAPI_BASE}/everything",
            params={
                "q":        query,
                "language": "en",
                "sortBy":   "publishedAt",
                "pageSize": page_size,
                "apiKey":   config.api.newsapi_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
    except Exception as e:
        logger.warning(f"NewsAPI query [{query[:30]}] failed: {e}")
        return []

    stories = []
    for article in articles:
        title   = article.get("title") or ""
        summary = article.get("description") or title
        url     = article.get("url") or ""
        source_name = (article.get("source") or {}).get("name", "NewsAPI")
        if not title or not url:
            continue
        if not is_relevant(title + " " + summary, source_name):
            continue

        published = _parse_published(article.get("publishedAt", ""))
        if not is_recent(published):
            continue

        stories.append(RawStory(
            id           = make_id(url),
            title        = title,
            summary      = summary[:500],
            url          = url,
            source       = SourceType.NEWSAPI,
            source_name  = source_name,
            published_at = published,
            score        = 65.0,
            keywords     = [k for k in config.content.keywords if k.lower() in title.lower()],
        ))
    return stories


def run_news_api_agent() -> list[RawStory]:
    """Main entry point — queries NewsAPI across the same topic beats as
    the RSS scrapers. Returns an empty list (logged, non-fatal) if
    NEWSAPI_KEY isn't configured."""
    if not config.api.newsapi_key:
        logger.info("NEWSAPI_KEY not set — skipping News API Agent")
        return []

    logger.info("Starting News API Agent...")
    all_stories: list[RawStory] = []
    for query in _QUERIES:
        found = fetch_everything(query)
        if found:
            logger.info(f"  NewsAPI [{query[:30]}]: {len(found)} stories")
        all_stories.extend(found)

    seen, unique = set(), []
    for s in all_stories:
        if s.id not in seen:
            seen.add(s.id)
            unique.append(s)

    logger.info(f"News API Agent complete: {len(unique)} unique stories")
    return unique
