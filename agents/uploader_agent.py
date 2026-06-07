import os
import json
import time
from datetime import datetime
from loguru import logger
from typing import Optional

from core.models import VideoPackage, VideoStatus
from config.settings import config

# ── YouTube category IDs ───────────────────────────────────
CATEGORY_ID = "25"   # 25 = News & Politics on YouTube

# ── Generate video metadata ────────────────────────────────
def build_metadata(package: VideoPackage) -> dict:
    story  = package.story.raw
    script = package.script

    # Title — SEO optimised
    title = f"{script.hook[:60]} | Meridian News Network"
    if len(title) > 100:
        title = title[:97] + "..."

    # Description — structured for YouTube SEO
    description = f"""{script.hook}

{script.what_happened}

{script.why_it_matters}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 MNN — Meridian News Network
"{config.channel.tagline}"

Covering AI, Cybersecurity, and Global Technology News
from Pakistan, the Middle East, Asia, and the World.

📌 Source: {story.source_name}
🔗 Original: {story.url}
📅 {datetime.now().strftime("%d %B %Y  |  %H:%M PKT")}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#MeridianNewsNetwork #MNN #AINews #TechNews #CyberSecurity
#BreakingNews #ArtificialIntelligence #GlobalNews
#PakistanNews #MiddleEast #AsiaNews #TechToday
"""

    # Tags — SEO keywords
    tags = [
        "Meridian News Network",
        "AI news", "artificial intelligence",
        "tech news today", "cybersecurity news",
        "breaking news", "global news",
        "Pakistan news", "Middle East news",
        "Asia tech news", "machine learning",
        "OpenAI", "technology news",
        story.source_name,
    ] + list(package.story.raw.keywords)[:5]

    return {
        "title":       title[:100],
        "description": description,
        "tags":        tags[:500],          # YouTube max 500 chars total
        "categoryId":  CATEGORY_ID,
        "defaultLanguage": config.channel.language,
        "privacyStatus": "public",
    }


# ── YouTube upload via API ─────────────────────────────────
def _youtube_upload(package: VideoPackage, metadata: dict) -> str:
    """Upload video + thumbnail to YouTube. Returns video URL."""

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        import google.oauth2.credentials

        # Load OAuth credentials from file
        creds_path = os.getenv("YOUTUBE_CREDENTIALS_PATH", "config/youtube_credentials.json")
        if not os.path.exists(creds_path):
            logger.warning(f"YouTube credentials not found at {creds_path} — mock upload")
            return _mock_upload(package, metadata)

        with open(creds_path) as f:
            creds_data = json.load(f)

        credentials = google.oauth2.credentials.Credentials(
            token         = creds_data.get("token"),
            refresh_token = creds_data.get("refresh_token"),
            token_uri     = creds_data.get("token_uri"),
            client_id     = creds_data.get("client_id"),
            client_secret = creds_data.get("client_secret"),
        )

        youtube = build("youtube", "v3", credentials=credentials)
        meta    = metadata

        # ── Upload video ───────────────────────────────────
        logger.info(f"Uploading video: {meta['title']}")

        request = youtube.videos().insert(
            part = "snippet,status",
            body = {
                "snippet": {
                    "title":           meta["title"],
                    "description":     meta["description"],
                    "tags":            meta["tags"],
                    "categoryId":      meta["categoryId"],
                    "defaultLanguage": meta["defaultLanguage"],
                },
                "status": {
                    "privacyStatus":  meta["privacyStatus"],
                    "selfDeclaredMadeForKids": False,
                },
            },
            media_body = MediaFileUpload(
                package.video.local_path,
                chunksize  = 1024 * 1024,   # 1 MB chunks
                resumable  = True,
                mimetype   = "video/mp4",
            ),
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info(f"Upload progress: {pct}%")

        video_id  = response["id"]
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"Video uploaded — {video_url}")

        # ── Upload thumbnail ───────────────────────────────
        if package.thumbnail and package.thumbnail.image_path:
            try:
                youtube.thumbnails().set(
                    videoId    = video_id,
                    media_body = MediaFileUpload(
                        package.thumbnail.image_path,
                        mimetype = "image/jpeg",
                    ),
                ).execute()
                logger.info("Thumbnail uploaded")
            except Exception as e:
                logger.warning(f"Thumbnail upload failed: {e}")

        return video_url

    except Exception as e:
        logger.error(f"YouTube upload failed: {e}")
        return _mock_upload(package, metadata)


# ── Mock upload (no credentials) ──────────────────────────
def _mock_upload(package: VideoPackage, metadata: dict) -> str:
    logger.info("Mock mode — simulating YouTube upload")

    output_dir = os.path.join(config.output_dir, "metadata")
    os.makedirs(output_dir, exist_ok=True)

    meta_path = os.path.join(
        output_dir,
        f"{package.story.raw.id}_metadata.json"
    )
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    logger.info(f"Mock metadata saved — {meta_path}")
    return f"https://youtube.com/watch?v=MOCK_{package.story.raw.id[:8]}"


# ── Main agent function ────────────────────────────────────
def run_uploader_agent(package: VideoPackage) -> str:
    logger.info(f"Uploader Agent — {package.story.raw.title[:50]}")

    metadata = build_metadata(package)
    package.title       = metadata["title"]
    package.description = metadata["description"]
    package.tags        = metadata["tags"]

    if (config.api.youtube_api_key
            and package.video.local_path
            and os.path.exists(package.video.local_path)
            and "mock" not in package.video.local_path):
        url = _youtube_upload(package, metadata)
    else:
        url = _mock_upload(package, metadata)

    if url:
        logger.info(f"Upload complete — {url}")
    else:
        logger.error("Upload failed")

    return url
