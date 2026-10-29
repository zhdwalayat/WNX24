"""agents/verification_agent.py — Fact-Checking Agent.

Deterministic scoring (no LLM reasoning involved) — per
https://agentfactory.panaversity.org/'s Gate 1a, computing a
confidence score from a fixed source-tier table and cross-source
count is "one exact step that is the same every time," not agent
work. This is where WNX24's journalism charter's "Multiple Sources"
and "Accuracy First" principles (see agents/editorial_standards.py)
actually get enforced — cross-source confirmation gates whether a
story ever reaches VERIFIED and becomes eligible for broadcast.

Source reliability follows the five-tier model professional
newsrooms use to mentally rank sources:
  Tier 1 (highest) — government statements, court documents, official
    press releases, direct interviews, live reporting, own reporters
  Tier 2 — major wire services & international newsrooms
  Tier 3 — verified organizations (research institutes, universities,
    NGOs, experts)
  Tier 4 — eyewitnesses, videos, photos, social media, aggregators —
    only after verification
  Tier 5 — rumours, anonymous posts, propaganda outlets — never
    publish directly
"""
from datetime import datetime

import httpx
from loguru import logger

from config.settings import config
from core.models import RawStory, VerificationStatus, VerifiedStory

# ── Source reliability tiers ────────────────────────────────
# Five-tier model professional newsrooms use to mentally rank sources:
#
#   Tier 1 (highest) — government statements, court documents, official
#     press releases, direct interviews, live reporting, own reporters.
#     WNX24 has no government/court scraper or original reporters, so
#     this is detected by domain (an official .gov/.int/UN/WHO/NATO URL
#     is what actually distinguishes a primary government statement from
#     a newsroom's *coverage* of one) rather than a source_name list.
#   Tier 2 — major wire services & international newsrooms.
#   Tier 3 — verified organizations: research institutes, universities,
#     NGOs, expert/specialist publications.
#   Tier 4 — eyewitnesses, videos, photos, social media, aggregators —
#     only after verification (cross-source confirmation required).
#   Tier 5 — rumours, anonymous posts, propaganda outlets — never
#     publish directly (hard-blocked, confidence forced to 0).
TIER_1_DOMAINS = (
    ".gov", ".gov.uk", ".gov.pk", ".un.org", ".who.int", ".nato.int",
)

TIER_2_SOURCES = {
    "Reuters", "Associated Press", "AP", "AP News",
    "Reuters World", "Reuters Health", "AFP",
    "BBC", "BBC World Service", "BBC News", "BBC World",
    "BBC Science", "BBC Health",
    "The Guardian", "Guardian World", "Guardian Science", "Guardian Climate",
    "Al Jazeera", "Al Jazeera English",
    "Dawn", "Dawn Pakistan", "Arab News",
    "Nikkei Asia", "Financial Times", "DW", "DW World",
    "France 24", "NHK World", "TRT World",
    "South China Morning Post", "Channel News Asia",
    "The Hindu", "Foreign Policy", "The Economist",
    "Gulf News", "Khaleej Times",
    "Wall Street Journal", "Bloomberg", "Bloomberg Tech", "CNBC",
    "Radio Free Europe", "Middle East Eye", "Al Monitor", "Al Arabiya",
    "Defense News", "Japan Times", "Caixin Global",
    "The News Pakistan", "Tribune Pakistan",
    "Washington Post", "NPR World", "ABC News World",
    "BBC Africa", "Africanews", "News24",
}

TIER_3_SOURCES = {
    "WHO", "WHO News", "NASA", "NASA News",
    "UN News",
    "Nature News", "New Scientist", "Science Daily",
    "STAT News", "MIT Tech Review", "IEEE Spectrum",
}

TIER_4_SOURCES = {
    "Geo News", "ARY News", "Samaa News",
    "CGTN", "Xinhua", "CNN", "Fox News",
    "Global Times", "China Daily", "Express Tribune", "APP News",
    "Hacker News", "Google News",   # aggregators — original publisher unknown
    "Wired", "Ars Technica", "The Verge",
    "MarketWatch", "Business Insider",
    "Economic Times", "Economic Times Tech", "TechCrunch",
    "VentureBeat", "ZDNet", "Breaking Defense", "OpenAI Blog", "MENAFN",
    "r/worldnews", "r/geopolitics", "r/economics",
    "r/artificial", "r/technology", "r/science",
    "AllAfrica",   # continental aggregator — original publisher unknown
}

TIER_5_SOURCES = {
    "RT", "Russia Today", "Sputnik", "PressTV",
}

# Pakistan breaking news rule:
# Pakistani source + ANY other Pakistani
# source confirmation = qualifies to air
PAKISTAN_SOURCES = {
    "Geo News", "ARY News", "Samaa News",
    "Dawn Pakistan", "The News", "Dawn",
    "The News Pakistan", "Express Tribune",
    "APP News", "Tribune Pakistan",
}

# ── Breaking-news impact scoring (deterministic) ────────────
# Standard newsroom "news values" (Galtung & Ruge; AP/Reuters breaking-
# alert practice) used to judge whether a story is significant enough to
# break with, independent of how much online engagement it has already
# picked up: magnitude/scale of harm, prominence of those involved,
# conflict, real-world policy consequence, and novelty/rarity. A story
# can be genuinely breaking before it has gone viral anywhere, and a
# merely viral story isn't automatically newsworthy — engagement score
# alone conflates popularity with significance.
_IMPACT_MAGNITUDE = (
    "killed", "dead", "deaths", "died", "casualties", "injured",
    "wounded", "missing", "evacuated", "destroyed", "devastated",
    "collapse", "collapsed", "toll",
)
_IMPACT_PROMINENCE = (
    "president", "prime minister", "king", "queen", "chancellor",
    "supreme court", "united nations", "un security council",
    "secretary-general", "central bank", "white house", "pentagon",
    "parliament", "congress",
)
_IMPACT_CONFLICT = (
    "war", "attack", "airstrike", "air strike", "invasion", "ceasefire",
    "explosion", "shooting", "military", "troops", "missile", "coup",
)
_IMPACT_CONSEQUENCE = (
    "resigns", "resignation", "sanctions", "state of emergency",
    "declares emergency", "impeachment", "sentenced", "banned",
    "shutdown", "market crash", "markets plunge", "stocks plunge",
    "stocks tumble", "rate hike", "rate cut", "cuts interest rates",
    "raises interest rates", "recession", "declares war",
)
_IMPACT_NOVELTY = (
    "unprecedented", "historic", "first time", "record", "worst since",
    "never before",
)


def compute_impact_score(story: RawStory) -> float:
    """Deterministic breaking-news impact score (0.0-1.0), independent of
    engagement/upvote score — see the news-values note above. Used
    alongside (never instead of) engagement score: a story qualifies as
    breaking if it clears either bar, not only the popularity one."""
    text = f"{story.title} {story.summary}".lower()
    score = 0.0
    if any(w in text for w in _IMPACT_MAGNITUDE):
        score += 0.40
    if any(w in text for w in _IMPACT_PROMINENCE):
        score += 0.20
    if any(w in text for w in _IMPACT_CONFLICT):
        score += 0.20
    if any(w in text for w in _IMPACT_CONSEQUENCE):
        score += 0.15
    if any(w in text for w in _IMPACT_NOVELTY):
        score += 0.10
    return min(round(score, 2), 1.0)


def _is_tier_1(url: str) -> bool:
    """Tier 1 is domain-based, not name-based — an official government/
    IGO URL is what actually makes something a primary statement."""
    host = (url or "").lower()
    return any(domain in host for domain in TIER_1_DOMAINS)


def _get_tier_boost(source_name: str, url: str = "") -> float:
    if source_name in TIER_5_SOURCES:
        return -1.0   # completely blocked
    if _is_tier_1(url):
        return +0.30
    if source_name in TIER_2_SOURCES:
        return +0.20
    if source_name in TIER_3_SOURCES:
        return +0.10
    if source_name in TIER_4_SOURCES:
        return 0.0    # no boost — only after verification
    return 0.0        # unknown source — neutral


# ── Helpers ───────────────────────────────────────────────
def compute_confidence(story: RawStory, cross_sources: list[str],
                        all_stories: list[RawStory]) -> float:
    score = 0.0

    # 1. Cross-source confirmation (increased weight — most important)
    if len(cross_sources) >= 3:
        score += 0.55
    elif len(cross_sources) == 2:
        score += 0.40
    elif len(cross_sources) == 1:
        score += 0.20

    # 2. Engagement score
    if story.score >= 100:
        score += 0.20
    elif story.score >= 50:
        score += 0.10
    elif story.score >= 10:
        score += 0.05

    # 3. Keyword relevance (increased weight)
    keyword_hits = len(story.keywords)
    if keyword_hits >= 4:
        score += 0.20
    elif keyword_hits >= 2:
        score += 0.15
    elif keyword_hits >= 1:
        score += 0.05

    # 4. Source trustworthiness
    trusted_sources = [
        # Tech
        "google news", "hacker news", "techcrunch",
        "wired", "ars technica", "the verge", "mit",
        "ieee", "venturebeat", "zdnet", "infoq",
        # Global mainstream
        "reuters", "bbc", "bloomberg", "financial times",
        "the guardian", "al jazeera", "associated press",
        "ap news", "france 24", "dw world", "deutsche welle",
        "washington post", "wall street journal", "the economist",
        "foreign policy", "radio free europe",
        # Markets
        "cnbc", "marketwatch", "forbes", "business insider",
        "economic times",
        # Science & health
        "nasa", "nature", "new scientist", "science daily",
        "who", "stat news", "medical news today",
        "bbc science", "bbc health", "reuters health",
        # Security
        "krebs", "bleeping computer", "dark reading",
        "securityweek", "threatpost", "cisa",
        # Conflict & affairs
        "defense news", "breaking defense", "crisis group",
        "middle east eye", "al arabiya", "al monitor",
        # South Asia
        "dawn", "the hindu", "ndtv", "tribune",
        # East Asia
        "nikkei", "south china morning post", "channel news asia",
        "japan times", "caixin",
        # Africa
        "africanews", "news24",
    ]
    if any(t in story.source_name.lower() for t in trusted_sources):
        score += 0.15

    # 5. Has a real URL
    if story.url.startswith("https://") and "reddit.com" not in story.url:
        score += 0.05

    # 6. Penalise low-interest topics
    low_interest = [
        "chrome update", "browser update", "minor update",
        "patch", "bug fix", "maintenance", "version",
    ]
    if any(phrase in story.title.lower() for phrase in low_interest):
        score -= 0.20

    # 7. Penalise press release / sponsored content language
    pr_phrases = [
        "proud to announce", "pleased to announce",
        "excited to announce", "delighted to announce",
        "launches new", "introduces new", "unveils new",
        "partnership with", "in partnership with",
        "sponsored by", "paid partnership",
        "press release", "for immediate release",
    ]
    if any(p in story.title.lower() or
           p in story.summary.lower()
           for p in pr_phrases):
        score -= 0.30
        logger.debug(f"PR content penalty applied: {story.title[:50]}")

    # ── Source tier boost ──────────────────────────────────────
    tier_boost = _get_tier_boost(story.source_name, story.url)
    if tier_boost == -1.0:
        logger.info(f"Tier 5 (blocked) source rejected: {story.source_name}")
        return 0.0
    score += tier_boost

    # ── Tier 4 (eyewitness/social/aggregator) requires
    # cross-confirmation — "only after verification" ──────────
    if story.source_name in TIER_4_SOURCES:
        if story.source_name in PAKISTAN_SOURCES:
            # Pakistan sources — cross-check with other
            # Pakistani sources
            if not _pakistan_cross_check(story, all_stories):
                score -= 0.20
                logger.debug(
                    f"Pakistan source — no cross-confirmation: "
                    f"{story.title[:50]}"
                )
        else:
            # Other Tier 4 — needs any cross-confirmation
            if not cross_sources:
                score -= 0.20

    return min(round(score, 2), 1.0)


def _pakistan_cross_check(story: RawStory, all_stories: list[RawStory]) -> bool:
    """
    For Pakistan stories — check if at least
    one other Pakistani source covers same story.
    """
    if story.source_name not in PAKISTAN_SOURCES:
        return False

    title_words = set(
        story.title.lower().split()
    ) - {"the", "a", "an", "is", "in", "of", "to", "and", "for"}

    for other in all_stories:
        if other.id == story.id:
            continue
        if other.source_name not in PAKISTAN_SOURCES:
            continue
        other_words = set(other.title.lower().split())
        overlap = len(title_words & other_words)
        if overlap >= 4:
            return True
    return False


def find_cross_sources(story: RawStory,
                       all_stories: list[RawStory]) -> list[str]:
    """Find other stories covering the same topic."""
    cross = []
    title_words = set(story.title.lower().split())

    for other in all_stories:
        if other.id == story.id:
            continue
        other_words = set(other.title.lower().split())

        # Check word overlap (at least 3 significant words match)
        overlap = title_words & other_words
        stopwords = {"the", "a", "an", "is", "in", "on",
                     "at", "to", "of", "and", "or", "for",
                     "with", "that", "this", "it", "as"}
        meaningful = overlap - stopwords

        if len(meaningful) >= 3:
            cross.append(other.source_name)

    return list(set(cross))


def check_url_accessible(url: str) -> bool:
    """Check if the article URL is reachable."""
    try:
        resp = httpx.head(url, timeout=5, follow_redirects=True)
        return resp.status_code < 400
    except Exception:
        return False


# ── Main agent function ────────────────────────────────────
def run_verification(stories: list[RawStory]) -> list[VerifiedStory]:
    logger.info(f"Verification Agent: checking {len(stories)} stories...")

    verified = []

    for story in stories:
        # 1. Find cross-source confirmation
        cross_sources = find_cross_sources(story, stories)

        # 2. Compute confidence score
        confidence = compute_confidence(story, cross_sources, stories)

        # 3. Decide status
        if confidence >= config.content.min_confidence_score:
            status = VerificationStatus.VERIFIED
        elif confidence >= 0.4:
            status = VerificationStatus.PENDING
        else:
            status = VerificationStatus.REJECTED

        verified.append(VerifiedStory(
            raw              = story,
            confidence_score = confidence,
            status           = status,
            cross_sources    = cross_sources,
            verified_at      = datetime.now(),
        ))

    # Stats
    v = sum(1 for s in verified if s.status == VerificationStatus.VERIFIED)
    p = sum(1 for s in verified if s.status == VerificationStatus.PENDING)
    r = sum(1 for s in verified if s.status == VerificationStatus.REJECTED)

    logger.info(f"Verification complete — "
                f"Verified: {v} | Pending: {p} | Rejected: {r}")

    # Return verified AND pending (never rejected/blocked) so both get
    # persisted by callers' save_stories() — core.database.get_fresh_stories
    # lets confirmed 'pending' stories (0.5+ confidence) into a show's
    # candidate pool at a lower bar than full verification, so genuinely
    # cross-confirmed but lower-volume regional coverage isn't silently
    # discarded before it ever reaches the database. Callers that need
    # only broadcast-ready stories (e.g. orchestrator.py's live-scrape
    # fallback, which skips the DB round-trip entirely) filter to
    # VerificationStatus.VERIFIED themselves — this function no longer
    # does that filtering for them.
    result = [s for s in verified
              if s.status in (VerificationStatus.VERIFIED, VerificationStatus.PENDING)]
    result.sort(key=lambda x: x.confidence_score, reverse=True)

    return result
