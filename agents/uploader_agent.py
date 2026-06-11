import os
import json
import time
from datetime import datetime
from loguru import logger
from typing import Optional

from core.models import VideoPackage, VideoStatus
from config.settings import config, ShowConfig

# ── YouTube category IDs ───────────────────────────────────
CATEGORY_ID = "25"   # 25 = News & Politics on YouTube

# ── Auto-generate YouTube timestamp chapters ───────────────
def build_chapters(script, show) -> str:
    wpm = config.content.words_per_minute

    def words_to_time(words: int) -> str:
        secs = int((words / wpm) * 60)
        mins = secs // 60
        secs = secs % 60
        return f"{mins}:{secs:02d}"

    chapters = ["0:00 Introduction"]
    offset   = 15   # words for hook + open

    if script.what_happened:
        chapters.append(f"{words_to_time(offset)} What Happened")
        offset += len(script.what_happened.split())

    if script.why_it_matters:
        chapters.append(f"{words_to_time(offset)} Why It Matters")
        offset += len(script.why_it_matters.split())

    if script.expert_insight:
        chapters.append(f"{words_to_time(offset)} Analysis")
        offset += len(script.expert_insight.split())

    chapters.append(f"{words_to_time(offset)} Sign Off")

    return "\n".join(chapters)

# ── Generate video metadata ────────────────────────────────
def build_metadata(package: VideoPackage, show: ShowConfig = None) -> dict:
    story  = package.story.raw
    script = package.script

    # Title — SEO optimised (strip any LLM-inserted "Hook:" prefix)
    hook_text = script.hook
    for prefix in ("hook:", "open:", "breaking:"):
        if hook_text.lower().startswith(prefix):
            hook_text = hook_text[len(prefix):].strip()
            break
    title = f"{hook_text[:60]} | Meridian News Network"
    if len(title) > 100:
        title = title[:97] + "..."

    # Description — structured for YouTube SEO
    chapters = build_chapters(script, show)

    description = f"""{script.hook}

{script.what_happened}

{script.why_it_matters}

{chapters}

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
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request

    except ImportError as _ie:
        logger.error(f"Google API import failed: {_ie}")
        return _mock_upload(package, metadata)

    SCOPES     = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]
    TOKEN_FILE = "config/youtube_token.json"
    OAUTH_FILE = "config/youtube_oauth.json"
    video_path = package.video.local_path

    if not video_path or not os.path.exists(video_path):
        logger.warning(f"Video file not found: {video_path} — using mock")
        return _mock_upload(package, metadata)

    # ── Load or refresh credentials ────────────────────────
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                import json as _json
                token_data = _json.load(f)
                creds = Credentials.from_authorized_user_info(
                    token_data, SCOPES
                )
        except Exception as e:
            logger.warning(f"Could not load token: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("YouTube token refreshed")
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
                creds = None

        if not creds:
            if not os.path.exists(OAUTH_FILE):
                logger.error(
                    f"OAuth client secrets not found: {OAUTH_FILE} — "
                    "download from Google Cloud Console and save as config/youtube_oauth.json"
                )
                return _mock_upload(package, metadata)
            logger.info("Opening browser for YouTube authorisation...")
            flow = InstalledAppFlow.from_client_secrets_file(
                OAUTH_FILE, SCOPES
            )
            creds = flow.run_local_server(
                port            = 0,
                open_browser    = True,
                success_message = (
                    "MNN authorised successfully. "
                    "You can close this tab."
                )
            )

        # Save refreshed/new token for future runs
        try:
            with open(TOKEN_FILE, "w") as f:
                import json as _json
                _json.dump(_json.loads(creds.to_json()), f, indent=2)
            logger.info(f"YouTube token saved — {TOKEN_FILE}")
        except Exception as e:
            logger.warning(f"Could not save token: {e}")

    try:
        # ── Build YouTube service ──────────────────────────────
        youtube = build("youtube", "v3", credentials=creds)

        # ── Upload video ───────────────────────────────────────
        body = {
            "snippet": {
                "title":       metadata["title"],
                "description": metadata["description"],
                "tags":        metadata["tags"],
                "categoryId":  metadata["categoryId"],
            },
            "status": {
                "privacyStatus":           metadata["privacyStatus"],
                "selfDeclaredMadeForKids": False,
            }
        }

        media = MediaFileUpload(
            video_path,
            mimetype  = "video/mp4",
            resumable = True,
            chunksize = 1024 * 1024 * 5,   # 5 MB chunks
        )

        logger.info(f"Uploading to YouTube: {metadata['title'][:60]}")

        request  = youtube.videos().insert(
            part       = "snippet,status",
            body       = body,
            media_body = media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info(f"Upload progress: {pct}%")

        video_id  = response["id"]
        video_url = f"https://youtube.com/watch?v={video_id}"
        logger.info(f"Upload complete — {video_url}")

        # ── Set thumbnail ──────────────────────────────────────
        thumb_path = package.thumbnail.image_path
        if thumb_path and os.path.exists(thumb_path):
            try:
                youtube.thumbnails().set(
                    videoId    = video_id,
                    media_body = MediaFileUpload(
                        thumb_path,
                        mimetype = "image/jpeg"
                    ),
                ).execute()
                logger.info(f"Thumbnail uploaded for {video_id}")
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
def run_uploader_agent(package: VideoPackage, show: ShowConfig = None) -> str:
    logger.info(f"Uploader Agent — {package.story.raw.title[:50]}")

    metadata = build_metadata(package, show)
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
