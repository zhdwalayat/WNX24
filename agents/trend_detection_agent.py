import httpx
import feedparser
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List
from loguru import logger

from core.models import RawStory, SourceType
from config.settings import config

# ── Helpers ───────────────────────────────────────────────
def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def is_relevant(text: str) -> bool:
    text_lower = text.lower()
    has_keyword = any(k.lower() in text_lower
                      for k in config.content.keywords)
    has_excluded = any(e.lower() in text_lower
                       for e in config.content.excluded_keywords)
    return has_keyword and not has_excluded

def is_recent(published: datetime) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=config.content.lookback_hours
    )
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published >= cutoff

# ── Scraper 1: Google News RSS ─────────────────────────────
def scrape_google_news() -> List[RawStory]:
    stories = []
    query = config.content.niche.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:20]:
            title   = entry.get("title", "")
            summary = entry.get("summary", title)
            link    = entry.get("link", "")

            if not is_relevant(title + " " + summary):
                continue

            published = datetime(*entry.published_parsed[:6],
                                 tzinfo=timezone.utc)
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
                score        = 50.0,
                keywords     = [k for k in config.content.keywords
                                if k.lower() in title.lower()],
            ))
        logger.info(f"Google News: {len(stories)} stories found")
    except Exception as e:
        logger.error(f"Google News scraper failed: {e}")

    return stories

# ── Scraper 2: Hacker News ─────────────────────────────────
def scrape_hacker_news() -> List[RawStory]:
    stories = []
    base = "https://hacker-news.firebaseio.com/v0"

    try:
        resp = httpx.get(f"{base}/topstories.json", timeout=10)
        top_ids = resp.json()[:50]

        for story_id in top_ids:
            item = httpx.get(f"{base}/item/{story_id}.json",
                             timeout=10).json()
            if not item or item.get("type") != "story":
                continue

            title = item.get("title", "")
            url   = item.get("url", f"https://news.ycombinator.com/item?id={story_id}")
            score = float(item.get("score", 0))

            if not is_relevant(title):
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

# ── Scraper 3: Reddit (RSS — no auth required) ────────────
def scrape_reddit() -> List[RawStory]:
    stories = []
    subreddits = ["artificial", "MachineLearning", "technology",
                  "worldnews", "geopolitics"]

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
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        published = datetime(*entry.published_parsed[:6],
                                             tzinfo=timezone.utc)
                    else:
                        published = datetime.now(timezone.utc)
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
                    score        = 40.0,
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
        # ── Tech focused ───────────────────────────────────────
        "TechCrunch":          "https://techcrunch.com/feed/",
        "Wired":               "https://www.wired.com/feed/rss",
        "Ars Technica":        "https://feeds.arstechnica.com/arstechnica/index",
        "The Verge":           "https://www.theverge.com/rss/index.xml",
        "MIT Tech Review":     "https://www.technologyreview.com/feed/",
        "VentureBeat":         "https://venturebeat.com/feed/",
        "ZDNet":               "https://www.zdnet.com/news/rss.xml",
        "InfoQ":               "https://feed.infoq.com/",

        # ── Global mainstream ──────────────────────────────────
        "BBC Technology":      "http://feeds.bbci.co.uk/news/technology/rss.xml",
        "Reuters Tech":        "https://feeds.reuters.com/reuters/technologyNews",
        "Associated Press":    "https://rsshub.app/ap/topics/technology",
        "Al Jazeera":          "https://www.aljazeera.com/xml/rss/all.xml",
        "The Guardian Tech":   "https://www.theguardian.com/technology/rss",
        "CNN Tech":            "http://rss.cnn.com/rss/edition_technology.rss",
        "Forbes Tech":         "https://www.forbes.com/innovation/feed/",
        "Washington Post Tech":"https://feeds.washingtonpost.com/rss/business/technology",
        "The Economist":       "https://www.economist.com/science-and-technology/rss.xml",
        "Deutsche Welle Tech": "https://rss.dw.com/xml/rss-en-tech",
        "Bloomberg Tech":      "https://feeds.bloomberg.com/technology/news.rss",
        "IEEE Spectrum":       "https://spectrum.ieee.org/feeds/feed.rss",

        # ── AI specific ────────────────────────────────────────
        "AI News":             "https://artificialintelligence-news.com/feed/",
        "Google AI Blog":      "https://blog.google/technology/ai/rss/",
        "OpenAI Blog":         "https://openai.com/blog/rss.xml",
        "DeepMind Blog":       "https://deepmind.google/blog/rss.xml",
        "Hugging Face Blog":   "https://huggingface.co/blog/feed.xml",
        "Towards Data Science":"https://towardsdatascience.com/feed",

        # ── Pakistan & South Asia ──────────────────────────────
        "Dawn Pakistan":       "https://www.dawn.com/feeds/home",
        "The News Pakistan":   "https://www.thenews.com.pk/rss/1/1",
        "Tribune Pakistan":    "https://tribune.com.pk/feed/",
        "Economic Times Tech": "https://economictimes.indiatimes.com/tech/rss",
        "The Hindu Tech":      "https://www.thehindu.com/sci-tech/technology/feeder/default.rss",
        "NDTV Tech":           "https://feeds.feedburner.com/ndtvnews-tech-gadgets",

        # ── Middle East ────────────────────────────────────────
        "Arab News Tech":      "https://www.arabnews.com/taxonomy/term/10251/rss.xml",
        "Gulf News Tech":      "https://gulfnews.com/rss/technology",
        "Khaleej Times":       "https://www.khaleejtimes.com/rss/technology",
        "Al Monitor":          "https://www.al-monitor.com/rss",
        "MENAFN":              "https://menafn.com/rss/rss.aspx",
        "Wamda":               "https://www.wamda.com/feed",

        # ── Far East & Asia Pacific ────────────────────────────
        "South China Morning Post": "https://www.scmp.com/rss/91/feed",
        "Nikkei Asia":         "https://asia.nikkei.com/rss/feed/nar",
        "Tech in Asia":        "https://www.techinasia.com/feed",
        "Channel News Asia":   "https://www.channelnewsasia.com/rssfeeds/8395744",
        "Japan Times":         "https://www.japantimes.co.jp/feed/",
        "Korea Herald":        "http://www.koreaherald.com/common/rss_xml.php?ct=102",
        "Caixin Global":       "https://www.caixinglobal.com/rss/",

        "KrASIA":              "https://kr-asia.com/feed",

        # ── World Affairs & Politics ──────────────────────
        "Foreign Policy":       "https://foreignpolicy.com/feed/",
        "The Economist":        "https://www.economist.com/international/rss.xml",
        "Middle East Eye":      "https://www.middleeasteye.net/rss",
        "Al Arabiya":           "https://english.alarabiya.net/tools/rss",
        "France 24":            "https://www.france24.com/en/rss",
        "DW World":             "https://rss.dw.com/xml/rss-en-world",
        "Radio Free Europe":    "https://www.rferl.org/api/epjfpqum",
        "AP News":              "https://feeds.apnews.com/rss/TopNews",

        # ── Economics & Markets ───────────────────────────
        "CNBC":                 "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "MarketWatch":          "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "Financial Times":      "https://www.ft.com/rss/home",
        "Wall Street Journal":  "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "Economic Times":       "https://economictimes.indiatimes.com/rssfeeds/1977021501.cms",
        "Business Insider":     "https://feeds.businessinsider.com/custom/all",

        # ── Science & Climate ─────────────────────────────
        "NASA News":            "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "New Scientist":        "https://www.newscientist.com/feed/home/",
        "Science Daily":        "https://www.sciencedaily.com/rss/all.xml",
        "Nature News":          "https://www.nature.com/nature.rss",
        "BBC Science":          "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "Guardian Science":     "https://www.theguardian.com/science/rss",
        "Guardian Climate":     "https://www.theguardian.com/environment/climate-crisis/rss",

        # ── Health & Medicine ─────────────────────────────
        "BBC Health":           "https://feeds.bbci.co.uk/news/health/rss.xml",
        "Reuters Health":       "https://feeds.reuters.com/reuters/healthNews",
        "STAT News":            "https://www.statnews.com/feed/",
        "Medical News Today":   "https://www.medicalnewstoday.com/rss",
        "WHO News":             "https://www.who.int/rss-feeds/news-english.xml",

        # ── War & Conflict ────────────────────────────────
        "Defense News":         "https://www.defensenews.com/arc/outboundfeeds/rss/",
        "Breaking Defense":     "https://breakingdefense.com/feed/",
        "Conflict Monitor":     "https://www.crisisgroup.org/rss.xml",
        "Reuters World":        "https://feeds.reuters.com/Reuters/worldNews",
    }

    for source_name, feed_url in feeds.items():
        try:
            feed = feedparser.parse(feed_url)
            count = 0

            for entry in feed.entries[:15]:
                title   = entry.get("title", "")
                summary = entry.get("summary", title)
                link    = entry.get("link", "")

                if not title or not link:
                    continue
                if not is_relevant(title + " " + summary):
                    continue

                # Parse published date
                try:
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        published = datetime(
                            *entry.published_parsed[:6],
                            tzinfo=timezone.utc
                        )
                    else:
                        published = datetime.now(timezone.utc)
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
                    score        = 60.0,
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

    # Remove duplicates by ID
    seen = set()
    unique = []
    for s in all_stories:
        if s.id not in seen:
            seen.add(s.id)
            unique.append(s)

    # Sort by score descending
    unique.sort(key=lambda x: x.score, reverse=True)

    logger.info(f"Trend Detection complete: {len(unique)} unique stories")
    return unique
