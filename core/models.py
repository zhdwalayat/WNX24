from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum

class SourceType(str, Enum):
    GOOGLE_NEWS   = "google_news"
    HACKER_NEWS   = "hacker_news"
    REDDIT        = "reddit"
    NEWSAPI       = "newsapi"
    RSS           = "rss"

class VerificationStatus(str, Enum):
    PENDING   = "pending"
    VERIFIED  = "verified"
    REJECTED  = "rejected"

class VideoStatus(str, Enum):
    PENDING    = "pending"
    SCRIPTED   = "scripted"
    RENDERED   = "rendered"
    UPLOADED   = "uploaded"
    FAILED     = "failed"

# ── Raw story from any scraper ─────────────────────────────
class RawStory(BaseModel):
    id:           str
    title:        str
    summary:      str
    url:          str
    source:       SourceType
    source_name:  str
    published_at: datetime
    score:        float = 0.0          # engagement / upvotes
    keywords:     List[str] = []

# ── After verification agent ───────────────────────────────
class VerifiedStory(BaseModel):
    raw:                RawStory
    confidence_score:   float
    status:             VerificationStatus
    cross_sources:      List[str] = []  # other sources that confirmed it
    verified_at:        datetime = Field(default_factory=datetime.utcnow)

# ── Script produced by script-writer agent ────────────────
class VideoScript(BaseModel):
    story_id:       str
    hook:           str
    what_happened:  str = ""
    why_it_matters: str = ""
    expert_insight: str = ""
    cta:            str = ""
    full_text:      str = ""
    word_count:     int = 0
    duration_secs:  int = 0
    created_at:     datetime = Field(default_factory=datetime.utcnow)

# ── Avatar video returned by HeyGen ───────────────────────
class AvatarVideo(BaseModel):
    script:        Optional[VideoScript] = None
    heygen_job_id: str = ""
    video_url:     str = ""
    local_path:    str = ""
    duration_secs: float = 0.0
    status:        VideoStatus = VideoStatus.PENDING
    created_at:    datetime = Field(default_factory=datetime.utcnow)

# ── Thumbnail image ────────────────────────────────────────
class Thumbnail(BaseModel):
    story_id:   str = ""
    headline:   str = ""
    image_path: str = ""
    url:        str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Backward-compat alias
    @property
    def local_path(self) -> str:
        return self.image_path

# ── Final video package ready for upload ──────────────────
class VideoPackage(BaseModel):
    story:       VerifiedStory
    script:      VideoScript
    video:       AvatarVideo
    thumbnail:   Thumbnail
    title:       str = ""
    description: str = ""
    tags:        List[str] = []
    status:      VideoStatus = VideoStatus.PENDING
    youtube_url: str = ""
    created_at:  datetime = Field(default_factory=datetime.now)
