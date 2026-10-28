"""agents/social_media_agent.py — Social Media Agent (PENDING).

Deliberately not implemented yet. Posting to Twitter/X, Instagram,
TikTok, or Facebook all require paid/approved developer accounts
(X API v2 Basic tier is paid; Meta Graph API needs app review; TikTok
Content Posting API needs business verification) — none of which this
project has configured, unlike the free-tier services every other
agent in this package uses (Groq, edge-tts, Wikimedia/NASA/ddgs, the
YouTube Data API's free quota).

Deferred to a later stage once those accounts exist. This stub exists
so orchestrator.py has a stable import target to wire in when that
happens, without anyone mistaking silence for "forgotten."
"""
from loguru import logger

from core.models import VideoPackage

SUPPORTED_PLATFORMS = ("twitter", "instagram", "tiktok", "facebook")


def run_social_media_agent(package: VideoPackage, platforms: list[str] = None) -> None:
    """Not implemented — raises so callers fail loudly instead of
    silently no-op-ing. Requires paid API access per platform:
      - Twitter/X: X API v2 Basic tier (paid)
      - Instagram/Facebook: Meta Graph API, requires app review
      - TikTok: Content Posting API, requires business verification
    """
    logger.warning(
        "Social Media Agent is pending — no platform credentials configured. "
        f"Requested platforms: {platforms or SUPPORTED_PLATFORMS}"
    )
    raise NotImplementedError(
        "social_media_agent is deferred until paid platform API access is "
        "configured (Twitter/X, Meta, TikTok). See module docstring."
    )
