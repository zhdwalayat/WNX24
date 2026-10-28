"""agents/trend_detection_agent.py — Trend Detection Agent.

Deterministic scraping (no LLM reasoning involved) — per
https://agentfactory.panaversity.org/'s Gate 1a, this is a fixed
"call these feeds/endpoints, keyword-filter the results" procedure,
so it follows the same plain-function style as news_api_agent.py
rather than wrapping an LLM around it.
"""
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

import feedparser
import httpx
from loguru import logger

from config.settings import config
from core.models import RawStory, SourceType


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
    "guardian world", "reuters", "associated press", "un news",
    "uk government", "bbc africa", "africanews", "news24",
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
    "Al Monitor":        70.0, "UN News":            72.0,
    "UK Government":     70.0,
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
    # Africa
    "BBC Africa":        78.0, "Africanews":         75.0,
    "News24":            68.0, "AllAfrica":          65.0,
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
    cutoff = datetime.now(UTC) - timedelta(
        hours=config.content.lookback_hours
    )
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return published >= cutoff

# ── Scraper 1: Google News RSS (multiple queries) ──────────
def scrape_google_news() -> list[RawStory]:
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
            # feedparser.parse(url) has no timeout of its own and can
            # hang indefinitely on a slow/unresponsive server — fetch
            # via httpx (bounded timeout) and hand feedparser the bytes.
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            feed = feedparser.parse(resp.content)
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
                                        tzinfo=UTC)
                except Exception:
                    published = datetime.now(UTC)
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
def scrape_hacker_news() -> list[RawStory]:
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
                item.get("time", 0), tz=UTC
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
def scrape_reddit() -> list[RawStory]:
    stories = []
    subreddits = ["worldnews", "geopolitics", "economics",
                  "artificial", "technology", "science"]

    for sub in subreddits:
        try:
            url  = f"https://www.reddit.com/r/{sub}/hot.rss?limit=20"
            # See scrape_google_news — feedparser.parse(url) has no
            # timeout and can hang indefinitely; fetch via httpx instead.
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            feed = feedparser.parse(resp.content)

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
                                        tzinfo=UTC) \
                        if hasattr(entry, "published_parsed") \
                        and entry.published_parsed \
                        else datetime.now(UTC)
                except Exception:
                    published = datetime.now(UTC)
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

# ── Generic RSS feed scraper ──────────────────────────────
def scrape_rss_feeds(feeds: dict) -> list[RawStory]:
    """Scrape an arbitrary {source_name: feed_url} dict of RSS feeds."""
    stories = []

    for source_name, feed_url in feeds.items():
        try:
            resp  = httpx.get(feed_url, timeout=10, follow_redirects=True)
            feed  = feedparser.parse(resp.content)
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
                        *entry.published_parsed[:6], tzinfo=UTC
                    ) if hasattr(entry, "published_parsed") \
                      and entry.published_parsed \
                      else datetime.now(UTC)
                except Exception:
                    published = datetime.now(UTC)
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

    return stories


# ── Scraper 4: Mainstream Media RSS ───────────────────────
def scrape_mainstream_media() -> list[RawStory]:
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
        # Tier 1 relies on the story URL's domain (.un.org, .who.int,
        # .gov, etc.) to detect an official government/IGO source —
        # these two feeds are what actually give it live un.org/gov.uk
        # data instead of source-tier entries no scraper ever produces.
        # (NATO's RSS feed URL is stale/404 as of this writing — left
        # out rather than added as a knowingly-dead entry.)
        "UN News":              "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        "UK Government":        "https://www.gov.uk/search/news-and-communications.atom",

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

        # ── Pakistan Media ──────────────────────────────────────────
        "Geo News":             "https://www.geo.tv/rss/1",
        "ARY News":             "https://arynews.tv/feed/",
        "Samaa News":           "https://www.samaa.tv/feed/",
        "APP News":             "https://www.app.com.pk/feed/",

        # ── Middle East & Asia ────────────────────────────────────
        "South China Morning Post": "https://www.scmp.com/rss/91/feed",
        "Nikkei Asia":          "https://asia.nikkei.com/rss/feed/nar",
        "Channel News Asia":    "https://www.channelnewsasia.com/rssfeeds/8395744",
        "Japan Times":          "https://www.japantimes.co.jp/feed/",
        "Caixin Global":        "https://www.caixinglobal.com/rss/",
        "MENAFN":               "https://menafn.com/rss/rss.aspx",

        # ── American World Impact ───────────────────────────────────
        "CNN World":            "http://rss.cnn.com/rss/edition_world.rss",
        "Washington Post":      "https://feeds.washingtonpost.com/rss/world",
        "NPR World":            "https://feeds.npr.org/1004/rss.xml",
        "ABC News World":       "https://abcnews.go.com/abcnews/internationalheadlines",

        # ── Chinese Media ────────────────────────────────────────────
        "CGTN":                 "https://www.cgtn.com/subscribe/rss/section/world.xml",
        "China Daily":          "https://www.chinadaily.com.cn/rss/world_rss.xml",

        # ── Additional Global ─────────────────────────────────────────
        "TRT World":            "https://www.trtworld.com/rss",
        "NHK World":            "https://www3.nhk.or.jp/rss/news/cat0.xml",
        "The Hindu":            "https://www.thehindu.com/feeder/default.rss",

        # ── Africa ─────────────────────────────────────────────────
        "BBC Africa":           "https://feeds.bbci.co.uk/news/world/africa/rss.xml",
        "Africanews":           "https://www.africanews.com/feed/rss",
        "AllAfrica":            "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
        "News24":               "https://feeds.news24.com/articles/news24/TopStories/rss",
    }

    stories = scrape_rss_feeds(feeds)
    logger.info(f"Mainstream Media: {len(stories)} total stories")
    return stories

# ── Main agent function ────────────────────────────────────
def run_trend_detection() -> list[RawStory]:
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
