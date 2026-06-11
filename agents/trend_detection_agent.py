import httpx
import feedparser
import hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from typing import List
from loguru import logger

from core.models import RawStory, SourceType
from config.settings import config

# ── Helpers ───────────────────────────────────────────────
def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

# Trusted sources that carry world news — bypass keyword filter
TRUSTED_GLOBAL = {
    "ap news", "reuters world", "bbc world", "bbc news", "al jazeera",
    "france 24", "dw world", "the economist", "foreign policy",
    "financial times", "wall street journal", "bloomberg", "cnbc",
    "dawn pakistan", "the news pakistan", "tribune pakistan",
    "middle east eye", "al monitor", "al arabiya", "radio free europe",
    "guardian world", "reuters", "associated press",
}

# Category base scores — world/economy/conflict compete with tech
CATEGORY_SCORES = {
    # World affairs
    "AP News":           80.0, "Reuters World":     80.0,
    "BBC World":         80.0, "BBC News":           80.0,
    "Al Jazeera":        78.0, "France 24":          75.0,
    "DW World":          75.0, "Foreign Policy":     75.0,
    "Radio Free Europe": 72.0, "Guardian World":     75.0,
    "Middle East Eye":   72.0, "Al Arabiya":         70.0,
    "Al Monitor":        70.0,
    # Economy & markets
    "Financial Times":   80.0, "Wall Street Journal":78.0,
    "Bloomberg":         78.0, "CNBC":               75.0,
    "MarketWatch":       72.0, "The Economist":      78.0,
    "Business Insider":  68.0, "Economic Times":     68.0,
    # Conflict
    "Reuters":           78.0, "Defense News":       70.0,
    "Breaking Defense":  68.0,
    # Science / health
    "Nature News":       70.0, "NASA News":          70.0,
    "BBC Science":       68.0, "BBC Health":         68.0,
    "WHO News":          70.0, "STAT News":          68.0,
    # South Asia
    "Dawn Pakistan":     72.0, "The News Pakistan":  68.0,
    "Tribune Pakistan":  68.0,
}

def get_rss_score(source_name: str) -> float:
    return CATEGORY_SCORES.get(source_name, 60.0)

def is_relevant(text: str, source_name: str = "") -> bool:
    text_lower  = text.lower()
    has_excluded = any(e.lower() in text_lower
                       for e in config.content.excluded_keywords)
    if has_excluded:
        return False
    # Trusted global sources don't need keyword match
    if source_name.lower() in TRUSTED_GLOBAL:
        return True
    return any(k.lower() in text_lower for k in config.content.keywords)

def is_recent(published: datetime) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=config.content.lookback_hours
    )
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published >= cutoff

# ── Scraper 1: Google News RSS (multiple queries) ──────────
def scrape_google_news() -> List[RawStory]:
    stories = []
    queries = [
        "world news today",
        "economy markets finance",
        "war conflict geopolitics",
        "AI technology",
        "science climate health",
    ]

    for query in queries:
        url = (f"https://news.google.com/rss/search"
               f"?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en")
        try:
            feed = feedparser.parse(url)
            added = 0
            for entry in feed.entries[:15]:
                title   = entry.get("title", "")
                summary = entry.get("summary", title)
                link    = entry.get("link", "")
                if not title or not link:
                    continue
                if not is_relevant(title + " " + summary, "google news"):
                    continue
                try:
                    published = datetime(*entry.published_parsed[:6],
                                        tzinfo=timezone.utc)
                except Exception:
                    published = datetime.now(timezone.utc)
                if not is_recent(published):
                    continue
                stories.append(RawStory(
                    id           = make_id(link),
                    title        = title,
                    summary      = summary[:500],
                    url          = link,
                    source       = SourceType.GOOGLE_NEWS,
                    source_name  = "Google News",
                    published_at = published,
                    score        = 70.0,
                    keywords     = [k for k in config.content.keywords
                                    if k.lower() in title.lower()],
                ))
                added += 1
            if added:
                logger.info(f"Google News [{query[:20]}]: {added} stories")
        except Exception as e:
            logger.error(f"Google News [{query}] failed: {e}")

    logger.info(f"Google News total: {len(stories)} stories")
    return stories

# ── Scraper 2: Hacker News (capped score) ─────────────────
def scrape_hacker_news() -> List[RawStory]:
    stories = []
    base = "https://hacker-news.firebaseio.com/v0"

    try:
        resp    = httpx.get(f"{base}/topstories.json", timeout=10)
        top_ids = resp.json()[:50]

        for story_id in top_ids:
            item = httpx.get(f"{base}/item/{story_id}.json",
                             timeout=10).json()
            if not item or item.get("type") != "story":
                continue

            title = item.get("title", "")
            url   = item.get("url",
                    f"https://news.ycombinator.com/item?id={story_id}")
            # Cap at 80 so HN tech stories don't crowd out world news
            raw_score = float(item.get("score", 0))
            score     = min(raw_score, 80.0)

            if not is_relevant(title, "hacker news"):
                continue

            published = datetime.fromtimestamp(
                item.get("time", 0), tz=timezone.utc
            )
            if not is_recent(published):
                continue

            stories.append(RawStory(
                id           = make_id(url),
                title        = title,
                summary      = title,
                url          = url,
                source       = SourceType.HACKER_NEWS,
                source_name  = "Hacker News",
                published_at = published,
                score        = score,
                keywords     = [k for k in config.content.keywords
                                if k.lower() in title.lower()],
            ))

        logger.info(f"Hacker News: {len(stories)} stories found")
    except Exception as e:
        logger.error(f"Hacker News scraper failed: {e}")

    return stories

# ── Scraper 3: Reddit ─────────────────────────────────────
def scrape_reddit() -> List[RawStory]:
    stories = []
    subreddits = ["worldnews", "geopolitics", "economics",
                  "artificial", "technology", "science"]

    for sub in subreddits:
        try:
            url  = f"https://www.reddit.com/r/{sub}/hot.rss?limit=20"
            feed = feedparser.parse(url)

            for entry in feed.entries[:20]:
                title   = entry.get("title", "")
                link    = entry.get("link", "")
                summary = entry.get("summary", title)
                if not title or not link:
                    continue
                if not is_relevant(title + " " + summary):
                    continue
                try:
                    published = datetime(*entry.published_parsed[:6],
                                        tzinfo=timezone.utc) \
                        if hasattr(entry, "published_parsed") \
                        and entry.published_parsed \
                        else datetime.now(timezone.utc)
                except Exception:
                    published = datetime.now(timezone.utc)
                if not is_recent(published):
                    continue

                stories.append(RawStory(
                    id           = make_id(link),
                    title        = title,
                    summary      = summary[:500],
                    url          = link,
                    source       = SourceType.REDDIT,
                    source_name  = f"r/{sub}",
                    published_at = published,
                    score        = 55.0,
                    keywords     = [k for k in config.content.keywords
                                    if k.lower() in title.lower()],
                ))
        except Exception as e:
            logger.error(f"Reddit r/{sub} failed: {e}")

    logger.info(f"Reddit: {len(stories)} stories found")
    return stories

# ── Scraper 4: Mainstream Media RSS ───────────────────────
def scrape_mainstream_media() -> List[RawStory]:
    stories = []

    feeds = {
        # ── World News (top priority — these lead the bulletin) ──
        "AP News":              "https://feeds.apnews.com/rss/TopNews",
        "Reuters World":        "https://feeds.reuters.com/Reuters/worldNews",
        "BBC World":            "http://feeds.bbci.co.uk/news/world/rss.xml",
        "Al Jazeera":           "https://www.aljazeera.com/xml/rss/all.xml",
        "France 24":            "https://www.france24.com/en/rss",
        "DW World":             "https://rss.dw.com/xml/rss-en-world",
        "Guardian World":       "https://www.theguardian.com/world/rss",
        "Radio Free Europe":    "https://www.rferl.org/api/epjfpqum",
        "Foreign Policy":       "https://foreignpolicy.com/feed/",

        # ── Economy & Markets ─────────────────────────────────────
        "Financial Times":      "https://www.ft.com/rss/home",
        "Wall Street Journal":  "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "Bloomberg":            "https://feeds.bloomberg.com/markets/news.rss",
        "CNBC":                 "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "MarketWatch":          "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "The Economist":        "https://www.economist.com/international/rss.xml",
        "Business Insider":     "https://feeds.businessinsider.com/custom/all",
        "Economic Times":       "https://economictimes.indiatimes.com/rssfeeds/1977021501.cms",

        # ── War & Conflict ────────────────────────────────────────
        "Middle East Eye":      "https://www.middleeasteye.net/rss",
        "Al Monitor":           "https://www.al-monitor.com/rss",
        "Al Arabiya":           "https://english.alarabiya.net/tools/rss",
        "Defense News":         "https://www.defensenews.com/arc/outboundfeeds/rss/",
        "Breaking Defense":     "https://breakingdefense.com/feed/",

        # ── Science & Climate ─────────────────────────────────────
        "Nature News":          "https://www.nature.com/nature.rss",
        "NASA News":            "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "New Scientist":        "https://www.newscientist.com/feed/home/",
        "Science Daily":        "https://www.sciencedaily.com/rss/all.xml",
        "BBC Science":          "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "Guardian Science":     "https://www.theguardian.com/science/rss",
        "Guardian Climate":     "https://www.theguardian.com/environment/climate-crisis/rss",

        # ── Health & Medicine ─────────────────────────────────────
        "BBC Health":           "https://feeds.bbci.co.uk/news/health/rss.xml",
        "WHO News":             "https://www.who.int/rss-feeds/news-english.xml",
        "STAT News":            "https://www.statnews.com/feed/",
        "Reuters Health":       "https://feeds.reuters.com/reuters/healthNews",

        # ── Technology & AI ───────────────────────────────────────
        "TechCrunch":           "https://techcrunch.com/feed/",
        "Wired":                "https://www.wired.com/feed/rss",
        "Ars Technica":         "https://feeds.arstechnica.com/arstechnica/index",
        "The Verge":            "https://www.theverge.com/rss/index.xml",
        "MIT Tech Review":      "https://www.technologyreview.com/feed/",
        "VentureBeat":          "https://venturebeat.com/feed/",
        "ZDNet":                "https://www.zdnet.com/news/rss.xml",
        "Bloomberg Tech":       "https://feeds.bloomberg.com/technology/news.rss",
        "IEEE Spectrum":        "https://spectrum.ieee.org/feeds/feed.rss",
        "OpenAI Blog":          "https://openai.com/blog/rss.xml",

        # ── Pakistan & South Asia ─────────────────────────────────
        "Dawn Pakistan":        "https://www.dawn.com/feeds/home",
        "The News Pakistan":    "https://www.thenews.com.pk/rss/1/1",
        "Tribune Pakistan":     "https://tribune.com.pk/feed/",
        "Economic Times Tech":  "https://economictimes.indiatimes.com/tech/rss",

        # ── Middle East & Asia ────────────────────────────────────
        "South China Morning Post": "https://www.scmp.com/rss/91/feed",
        "Nikkei Asia":          "https://asia.nikkei.com/rss/feed/nar",
        "Channel News Asia":    "https://www.channelnewsasia.com/rssfeeds/8395744",
        "Japan Times":          "https://www.japantimes.co.jp/feed/",
        "Caixin Global":        "https://www.caixinglobal.com/rss/",
        "MENAFN":               "https://menafn.com/rss/rss.aspx",
    }

    for source_name, feed_url in feeds.items():
        try:
            feed  = feedparser.parse(feed_url)
            score = get_rss_score(source_name)
            count = 0

            for entry in feed.entries[:15]:
                title   = entry.get("title", "")
                summary = entry.get("summary", title)
                link    = entry.get("link", "")
                if not title or not link:
                    continue
                if not is_relevant(title + " " + summary, source_name):
                    continue
                try:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ) if hasattr(entry, "published_parsed") \
                      and entry.published_parsed \
                      else datetime.now(timezone.utc)
                except Exception:
                    published = datetime.now(timezone.utc)
                if not is_recent(published):
                    continue

                stories.append(RawStory(
                    id           = make_id(link),
                    title        = title,
                    summary      = summary[:500],
                    url          = link,
                    source       = SourceType.RSS,
                    source_name  = source_name,
                    published_at = published,
                    score        = score,
                    keywords     = [k for k in config.content.keywords
                                    if k.lower() in title.lower()],
                ))
                count += 1

            if count:
                logger.info(f"  {source_name}: {count} stories")
        except Exception as e:
            logger.warning(f"  {source_name} feed failed: {e}")

    logger.info(f"Mainstream Media: {len(stories)} total stories")
    return stories

# ── Main agent function ────────────────────────────────────
def run_trend_detection() -> List[RawStory]:
    logger.info("Starting Trend Detection Agent...")

    all_stories = []
    all_stories.extend(scrape_google_news())
    all_stories.extend(scrape_hacker_news())
    all_stories.extend(scrape_reddit())
    all_stories.extend(scrape_mainstream_media())

    # Deduplicate by ID
    seen, unique = set(), []
    for s in all_stories:
        if s.id not in seen:
            seen.add(s.id)
            unique.append(s)

    # Sort by score descending
    unique.sort(key=lambda x: x.score, reverse=True)

    logger.info(f"Trend Detection complete: {len(unique)} unique stories")
    return unique
