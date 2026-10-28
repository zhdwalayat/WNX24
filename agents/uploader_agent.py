"""agents/uploader_agent.py — Publishing Agent.

Deterministic API-call agent (no LLM reasoning involved) — per
https://agentfactory.panaversity.org/'s Gate 1a, uploading via the
YouTube Data API and applying a fixed video-retention policy is
"one exact step that is the same every time," not agent work.
"""
import json
import os
from datetime import UTC, datetime

from loguru import logger

from agents.seo_agent import build_metadata
from config.settings import ShowConfig, config
from core.models import VideoPackage

# ── Video retention policy ──────────────────────────────────
# Shows to NEVER delete — permanent content
KEEP_FOREVER = {"deep_dive", "primetime", "week_review", "explainer"}

# Other shows — delete videos older than this many days
CLEAN_AFTER_DAYS = {
    "breaking":      1,
    "morning":       3,
    "midday":        3,
    "evening":       3,
    "summary":       3,
    "pakistan":      3,
    "asia":          3,
    "mideast":       3,
    "economy":       3,
    "science":       5,
    "health":        5,
    "conflict":      3,
    "world_affairs": 3,
}


# ── Shared authenticated YouTube client ─────────────────────
def get_youtube_client():
    """Build an authenticated YouTube Data API v3 client from the saved
    OAuth token, refreshing and persisting it if expired. Returns None
    if no valid/refreshable token exists — callers (cleanup_old_videos,
    agents/performance_agent.py) should skip gracefully rather than
    launch an interactive browser flow; see reauthorize() for that."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as e:
        logger.error(f"Google API import failed: {e}")
        return None

    SCOPES     = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]
    TOKEN_FILE = "config/youtube_token.json"

    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        with open(TOKEN_FILE) as f:
            creds = Credentials.from_authorized_user_info(json.load(f), SCOPES)
    except Exception as e:
        logger.warning(f"Could not load YouTube token: {e}")
        return None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, "w") as f:
                    json.dump(json.loads(creds.to_json()), f, indent=2)
            except Exception as e:
                logger.warning(f"YouTube token refresh failed: {e}")
                return None
        else:
            return None

    return build("youtube", "v3", credentials=creds)


# ── YouTube upload via API ─────────────────────────────────
def _youtube_upload(package: VideoPackage, metadata: dict) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

    except ImportError as _ie:
        logger.error(f"Google API import failed: {_ie}")
        return _mock_upload(package, metadata)

    SCOPES     = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]
    TOKEN_FILE = "config/youtube_token.json"
    video_path = package.video.local_path

    if not video_path or not os.path.exists(video_path):
        logger.warning(f"Video file not found: {video_path} — using mock")
        return _mock_upload(package, metadata)

    # ── Load or refresh credentials ────────────────────────
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
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
            # No valid/refreshable token. Running an interactive
            # browser OAuth flow here would block the unattended
            # 24/7 pipeline indefinitely, so fall back to mock and
            # let the operator re-authorise out-of-band (see
            # `python -m agents.uploader_agent --reauth`).
            logger.error(
                "YouTube token missing or unrefreshable — run "
                "'.venv/Scripts/python.exe -m agents.uploader_agent --reauth' "
                "to re-authorise. Falling back to mock upload."
            )
            return _mock_upload(package, metadata)

        # Save refreshed token for future runs
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

    from core.database import count_real_uploads
    uploads_24h = count_real_uploads(hours=24)

    if (config.api.youtube_api_key
            and package.video.local_path
            and os.path.exists(package.video.local_path)
            and "mock" not in package.video.local_path
            and uploads_24h < config.channel.max_daily_uploads):
        url = _youtube_upload(package, metadata)
    else:
        if uploads_24h >= config.channel.max_daily_uploads:
            logger.warning(
                f"Daily YouTube upload cap reached "
                f"({uploads_24h}/{config.channel.max_daily_uploads} in last 24h) — using mock"
            )
        url = _mock_upload(package, metadata)

    if url:
        logger.info(f"Upload complete — {url}")
    else:
        logger.error("Upload failed")

    return url


# ── Cleanup old YouTube videos ─────────────────────────────
def cleanup_old_videos(
    days_to_keep:      int  = 3,
    show_ids_to_clean: list = None,
    dry_run:           bool = True,
) -> int:
    """
    Delete YouTube videos past their show's retention window.

    KEEP_FOREVER shows (deep_dive, primetime, week_review,
    explainer) are never touched. Other shows are deleted once
    older than CLEAN_AFTER_DAYS[show_id], falling back to
    days_to_keep for any show not listed there. Pass
    show_ids_to_clean to restrict cleanup to specific shows.
    Videos whose show can't be matched via the broadcasts table
    are left alone.

    dry_run=True (default) only logs what would be deleted —
    pass dry_run=False to actually call videos().delete().
    Returns the number of videos deleted (or that would be,
    in dry-run mode).
    """
    try:
        from core.database import get_conn

        youtube = get_youtube_client()
        if youtube is None:
            logger.warning("No usable YouTube token — skipping cleanup")
            return 0

        channel_id = os.getenv("YOUTUBE_CHANNEL_ID", "")
        if not channel_id:
            logger.warning("No channel ID — skipping cleanup")
            return 0

        now     = datetime.now(UTC)
        deleted = 0
        conn    = get_conn()

        response = youtube.search().list(
            part       = "id,snippet",
            channelId  = channel_id,
            type       = "video",
            order      = "date",
            maxResults = 50,
        ).execute()

        for item in response.get("items", []):
            video_id  = item["id"]["videoId"]
            title     = item["snippet"]["title"]
            published = item["snippet"]["publishedAt"]

            pub_date = datetime.fromisoformat(
                published.replace("Z", "+00:00")
            )
            age_days = (now - pub_date).days

            row = conn.execute(
                "SELECT show_id FROM broadcasts WHERE youtube_url LIKE ?",
                (f"%{video_id}%",)
            ).fetchone()
            show_id = row["show_id"] if row else None

            if show_id is None or show_id in KEEP_FOREVER:
                continue
            if show_ids_to_clean is not None and show_id not in show_ids_to_clean:
                continue

            max_days = CLEAN_AFTER_DAYS.get(show_id, days_to_keep)
            if age_days < max_days:
                continue

            if dry_run:
                logger.info(
                    f"[dry run] Would delete ({age_days}d, {show_id}): "
                    f"{title[:50]}"
                )
                deleted += 1
                continue

            try:
                youtube.videos().delete(id=video_id).execute()
                logger.info(f"Deleted ({age_days}d, {show_id}): {title[:50]}")
                deleted += 1
            except Exception as e:
                logger.warning(f"Could not delete {video_id}: {e}")

        conn.close()
        logger.info(
            f"Cleanup complete — {deleted} videos "
            f"{'would be ' if dry_run else ''}deleted"
        )
        return deleted

    except Exception as e:
        logger.error(f"YouTube cleanup failed: {e}")
        return 0


# ── Manual re-authorisation (run this, not the pipeline) ───
def reauthorize() -> None:
    """
    Interactively re-authorise the YouTube OAuth token.

    The unattended pipeline never launches a browser flow itself
    (it falls back to mock uploads if the token is missing or
    unrefreshable) — run this manually whenever
    config/youtube_token.json is expired/revoked:

        .venv/Scripts/python.exe -m agents.uploader_agent --reauth
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES     = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]
    TOKEN_FILE = "config/youtube_token.json"
    OAUTH_FILE = "config/youtube_oauth.json"

    if not os.path.exists(OAUTH_FILE):
        logger.error(
            f"OAuth client secrets not found: {OAUTH_FILE} — "
            "download from Google Cloud Console and save as config/youtube_oauth.json"
        )
        return

    flow  = InstalledAppFlow.from_client_secrets_file(OAUTH_FILE, SCOPES)
    creds = flow.run_local_server(
        port            = 0,
        open_browser    = True,
        success_message = "WNX24 authorised successfully. You can close this tab."
    )

    with open(TOKEN_FILE, "w") as f:
        json.dump(json.loads(creds.to_json()), f, indent=2)

    logger.info(f"YouTube token saved — {TOKEN_FILE}")


if __name__ == "__main__":
    import sys
    if "--reauth" in sys.argv:
        reauthorize()
    else:
        print("Usage: python -m agents.uploader_agent --reauth")
