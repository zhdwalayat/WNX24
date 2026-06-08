import httpx
from datetime import datetime
from typing import List
from loguru import logger

from core.models import RawStory, VerifiedStory, VerificationStatus
from config.settings import config

# ── Helpers ───────────────────────────────────────────────
def compute_confidence(story: RawStory, cross_sources: List[str]) -> float:
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
    if any(l in story.title.lower() for l in low_interest):
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

    return min(round(score, 2), 1.0)


def find_cross_sources(story: RawStory,
                       all_stories: List[RawStory]) -> List[str]:
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
def run_verification(stories: List[RawStory]) -> List[VerifiedStory]:
    logger.info(f"Verification Agent: checking {len(stories)} stories...")

    verified = []

    for story in stories:
        # 1. Find cross-source confirmation
        cross_sources = find_cross_sources(story, stories)

        # 2. Compute confidence score
        confidence = compute_confidence(story, cross_sources)

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

    # Return only verified, sorted by confidence
    result = [s for s in verified
              if s.status == VerificationStatus.VERIFIED]
    result.sort(key=lambda x: x.confidence_score, reverse=True)

    return result
