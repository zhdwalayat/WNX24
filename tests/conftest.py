from datetime import UTC, datetime

import pytest

from core.models import RawStory, SourceType, VerificationStatus, VerifiedStory


@pytest.fixture
def make_verified_story():
    """Factory fixture — build a VerifiedStory with sensible defaults,
    overridable per test."""
    def _make(
        *,
        story_id: str = "story-1",
        title: str = "Pakistan rupee steadies after IMF review",
        summary: str = "",
        source_name: str = "Dawn Pakistan",
        keywords=None,
        confidence: float = 0.9,
    ) -> VerifiedStory:
        raw = RawStory(
            id           = story_id,
            title        = title,
            summary      = summary,
            url          = "https://example.com/story-1",
            source       = SourceType.RSS,
            source_name  = source_name,
            published_at = datetime.now(UTC),
            keywords     = keywords or [],
        )
        return VerifiedStory(
            raw=raw, confidence_score=confidence, status=VerificationStatus.VERIFIED,
        )
    return _make


@pytest.fixture
def verified_story(make_verified_story) -> VerifiedStory:
    return make_verified_story(keywords=["Pakistan", "IMF", "rupee"])


@pytest.fixture
def breaking_show():
    from config.settings import config
    return config.scheduler.shows["breaking"]
