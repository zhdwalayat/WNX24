# utils/scrapers.py
import httpx
import feedparser
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List
from loguru import logger
from config.settings import config

def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def is_relevant(text: str) -> bool:
    text_lower = text.lower()
    has_keyword  = any(k.lower() in text_lower for k in config.content.keywords)
    has_excluded = any(e.lower() in text_lower for e in config.content.excluded_keywords)
    return has_keyword and not has_excluded

def is_recent(published: datetime, hours: int = None) -> bool:
    h = hours or config.content.lookback_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=h)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published >= cutoff

def parse_rss_date(entry) -> datetime:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)

def fetch_rss(url: str, source_name: str, max_items: int = 15) -> List[dict]:
    results = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_items]:
            title   = entry.get("title", "")
            summary = entry.get("summary", title)
            link    = entry.get("link", "")
            if not title or not link:
                continue
            if not is_relevant(title + " " + summary):
                continue
            published = parse_rss_date(entry)
            if not is_recent(published):
                continue
            results.append({
                "id":           make_id(link),
                "title":        title,
                "summary":      summary[:500],
                "url":          link,
                "source_name":  source_name,
                "published_at": published,
                "score":        60.0,
                "keywords":     [k for k in config.content.keywords
                                 if k.lower() in title.lower()],
            })
    except Exception as e:
        logger.warning(f"RSS fetch failed [{source_name}]: {e}")
    return results

def fetch_hacker_news(max_items: int = 50) -> List[dict]:
    results = []
    base = "https://hacker-news.firebaseio.com/v0"
    try:
        ids = httpx.get(f"{base}/topstories.json", timeout=10).json()[:max_items]
        for sid in ids:
            item = httpx.get(f"{base}/item/{sid}.json", timeout=10).json()
            if not item or item.get("type") != "story":
                continue
            title = item.get("title", "")
            url   = item.get("url", f"https://news.ycombinator.com/item?id={sid}")
            score = float(item.get("score", 0))
            if not is_relevant(title):
                continue
            published = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)
            if not is_recent(published):
                continue
            results.append({
                "id":           make_id(url),
                "title":        title,
                "summary":      title,
                "url":          url,
                "source_name":  "Hacker News",
                "published_at": published,
                "score":        score,
                "keywords":     [k for k in config.content.keywords
                                 if k.lower() in title.lower()],
            })
    except Exception as e:
        logger.error(f"Hacker News scraper failed: {e}")
    return results

def fetch_reddit(subreddits: List[str] = None) -> List[dict]:
    subs = subreddits or ["artificial", "cybersecurity",
                           "MachineLearning", "netsec", "technology"]
    results = []
    headers = {"User-Agent": "meridian-news-bot/1.0"}
    for sub in subs:
        try:
            url  = f"https://www.reddit.com/r/{sub}/hot.json?limit=20"
            resp = httpx.get(url, headers=headers, timeout=10)
            for post in resp.json()["data"]["children"]:
                p     = post["data"]
                title = p.get("title", "")
                link  = "https://reddit.com" + p.get("permalink", "")
                text  = p.get("selftext", "")
                score = float(p.get("score", 0))
                if not is_relevant(title + " " + text):
                    continue
                published = datetime.fromtimestamp(
                    p.get("created_utc", 0), tz=timezone.utc
                )
                if not is_recent(published):
                    continue
                results.append({
                    "id":           make_id(link),
                    "title":        title,
                    "summary":      text[:500] if text else title,
                    "url":          link,
                    "source_name":  f"r/{sub}",
                    "published_at": published,
                    "score":        score,
                    "keywords":     [k for k in config.content.keywords
                                     if k.lower() in title.lower()],
                })
        except Exception as e:
            logger.error(f"Reddit r/{sub} failed: {e}")
    return results
