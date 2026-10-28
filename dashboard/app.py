"""
dashboard/app.py — WNX24 Control Room
Interactive dashboard with live pipeline execution.

Run:  uvicorn dashboard.app:app --reload --port 8000
Open: http://localhost:8000
"""

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── Allow imports from project root ───────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import config

app = FastAPI(title="WNX24 Control Room")

# ── Static files & templates ───────────────────────────────
BASE      = Path(os.path.dirname(os.path.abspath(__file__)))
OUTPUT    = Path("output")
THUMB_DIR = OUTPUT / "thumbnails"
VIDEO_DIR = OUTPUT / "videos"
META_DIR  = OUTPUT / "metadata"

app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# ── In-memory state ────────────────────────────────────────
broadcast_log:     list[dict]     = []
broadcast_history: list[dict]     = []
current_run:       dict[str, Any] = {}
is_running:        bool           = False

# ── Broadcast deduplication ────────────────────────────────
_HISTORY_FILE = Path("output/metadata/broadcast_history.json")

def _load_broadcast_ids() -> set:
    try:
        if _HISTORY_FILE.exists():
            return set(json.loads(_HISTORY_FILE.read_text()))
    except Exception:
        pass
    return set()

def _save_broadcast_ids(ids: set) -> None:
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_FILE.write_text(json.dumps(list(ids), indent=2))
    except Exception:
        pass

_broadcast_ids: set = _load_broadcast_ids()


def _log(msg: str, status: str = "info"):
    entry = {
        "time":   datetime.now().strftime("%H:%M:%S"),
        "msg":    msg,
        "status": status,   # info | success | error | warning
    }
    broadcast_log.append(entry)
    if len(broadcast_log) > 300:
        broadcast_log.pop(0)


# ── Background pipeline ────────────────────────────────────
async def run_pipeline(show_id: str):
    global is_running, current_run, broadcast_log

    is_running    = True
    broadcast_log = []
    current_run   = {
        "show_id":    show_id,
        "started_at": datetime.now().isoformat(),
    }

    try:
        show = config.scheduler.shows.get(show_id)
        if not show:
            _log(f"Unknown show: {show_id}", "error")
            is_running = False
            return

        _log(f"Starting broadcast — {show.name}", "info")
        _log(f"Target: {show.video_duration}s | {show.story_count} stories", "info")

        loop = asyncio.get_event_loop()

        # ── Step 1: Trend Detection ────────────────────────
        _log("[ 1/6 ]  Trend Detection — scraping 50+ sources...", "info")
        from agents.trend_detection_agent import run_trend_detection
        stories = await loop.run_in_executor(None, run_trend_detection)
        _log(f"         {len(stories)} stories scraped", "success")

        # ── Step 2: Verification ───────────────────────────
        _log("[ 2/6 ]  Verification — scoring & cross-checking...", "info")
        from agents.verification_agent import run_verification
        verified = await loop.run_in_executor(None, run_verification, stories)

        v = sum(1 for s in verified if s.status.value == "verified")
        r = sum(1 for s in verified if s.status.value == "rejected")
        _log(f"         {v} verified | {r} rejected", "success")

        fresh = [s for s in verified if s.status.value == "verified"]
        if not fresh:
            _log("No verified stories — aborting broadcast", "error")
            is_running = False
            return

        # Filter out stories already broadcast (deduplication)
        fresh = [s for s in fresh if s.raw.id not in _broadcast_ids]
        if not fresh:
            _log("All top stories already broadcast — no new content", "warning")
            is_running = False
            return

        top = fresh[0]
        _log(f"         Top story: {top.raw.title[:65]}", "success")
        _log(f"         Confidence: {top.confidence_score:.2f} | Sources: {len(top.cross_sources)}", "info")

        current_run["top_story"] = {
            "title":      top.raw.title,
            "source":     top.raw.source_name,
            "url":        top.raw.url,
            "confidence": round(top.confidence_score, 2),
            "score":      top.raw.score,
        }

        # ── Step 3: Script Writer ──────────────────────────
        _log("[ 3/6 ]  Script Writer — generating broadcast script...", "info")
        from agents.script_writer_agent import run_script_writer

        def _write():
            return run_script_writer(fresh[:show.story_count], show)

        script = await loop.run_in_executor(None, _write)
        _log(f"         {script.word_count} words | ~{script.duration_secs}s", "success")

        current_run["script"] = {
            "hook":           script.hook,
            "what_happened":  script.what_happened,
            "why_it_matters": script.why_it_matters,
            "expert_insight": script.expert_insight,
            "cta":            script.cta,
            "word_count":     script.word_count,
            "duration_secs":  script.duration_secs,
        }

        # ── Step 4: Avatar Generator ───────────────────────
        _log("[ 4/6 ]  Avatar Generator — rendering video...", "info")
        from agents.avatar_agent import run_avatar_agent
        video = await loop.run_in_executor(None, run_avatar_agent, script)

        if config.api.heygen_api_key:
            _log(f"         Video rendered — {video.local_path}", "success")
        else:
            _log("         Mock mode — add HEYGEN_API_KEY for real video", "warning")

        current_run["video"] = {
            "status":   video.status.value,
            "job_id":   video.heygen_job_id,
            "local":    video.local_path,
            "duration": video.duration_secs,
        }

        # ── Step 5: Thumbnail ──────────────────────────────
        _log("[ 5/6 ]  Thumbnail — generating broadcast image...", "info")
        from agents.thumbnail_agent import run_thumbnail_agent

        def _thumb():
            return run_thumbnail_agent(top, script, show_id)

        thumbnail = await loop.run_in_executor(None, _thumb)
        _log(f"         Saved — {thumbnail.image_path}", "success")

        current_run["thumbnail"] = {
            "path":     thumbnail.image_path,
            "filename": os.path.basename(thumbnail.image_path),
            "headline": thumbnail.headline,
        }

        # ── Step 6: Upload ─────────────────────────────────
        _log("[ 6/6 ]  Uploader — publishing to YouTube...", "info")
        from agents.uploader_agent import run_uploader_agent
        from core.models import VideoPackage

        package = VideoPackage(
            story=top, script=script, video=video, thumbnail=thumbnail
        )

        url = await loop.run_in_executor(None, run_uploader_agent, package, show)

        if config.api.youtube_api_key:
            _log(f"         Uploaded — {url}", "success")
        else:
            _log("         Mock upload — add YOUTUBE_API_KEY for real upload", "warning")

        current_run["youtube_url"] = url
        current_run["title"]       = package.title or script.hook
        current_run["completed_at"] = datetime.now().isoformat()

        # Mark story as broadcast so it won't be re-uploaded
        for s in fresh[:show.story_count]:
            _broadcast_ids.add(s.raw.id)
        _save_broadcast_ids(_broadcast_ids)

        _log("", "info")
        _log(f"BROADCAST COMPLETE — {show.name}", "success")
        _log(f"Title: {current_run['title']}", "success")

        # Save to history
        broadcast_history.insert(0, {
            "show":      show.name,
            "show_id":   show_id,
            "title":     current_run.get("title", ""),
            "story":     top.raw.title,
            "words":     script.word_count,
            "duration":  script.duration_secs,
            "thumbnail": current_run["thumbnail"]["filename"],
            "url":       url,
            "time":      datetime.now().strftime("%H:%M:%S"),
        })

    except Exception as e:
        _log(f"Pipeline error: {e}", "error")

    finally:
        is_running = False


# ── Routes ─────────────────────────────────────────────────

@app.get("/")
async def index(request: Request):
    shows = {
        sid: show.name
        for sid, show in config.scheduler.shows.items()
    }
    return templates.TemplateResponse(request, "index.html", {
        "channel": config.channel.name,
        "tagline": config.channel.tagline,
        "anchor":  config.anchor.name,
        "shows":   shows,
    })


@app.post("/run/{show_id}")
async def run_show(show_id: str, background_tasks: BackgroundTasks):
    global is_running
    if is_running:
        return JSONResponse({"error": "A broadcast is already running"}, status_code=409)

    valid = set(config.scheduler.shows.keys())
    if show_id not in valid:
        return JSONResponse({"error": f"Unknown show: {show_id}"}, status_code=400)

    background_tasks.add_task(run_pipeline, show_id)
    return JSONResponse({"status": "started", "show": show_id})


@app.get("/status")
async def status():
    return JSONResponse({
        "is_running":  is_running,
        "log":         broadcast_log,
        "current_run": current_run,
        "history":     broadcast_history,
    })


@app.get("/thumbnail/{filename}")
async def serve_thumbnail(filename: str):
    filename = Path(filename).name
    path = THUMB_DIR / filename
    if path.exists():
        return FileResponse(str(path), media_type="image/jpeg")
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.get("/video/{filename}")
async def serve_video(filename: str):
    filename = Path(filename).name
    path = VIDEO_DIR / filename
    if path.exists():
        return FileResponse(str(path), media_type="video/mp4")
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.get("/outputs")
async def list_outputs():
    """Scan output directories and return all generated broadcasts."""
    items = []
    if not VIDEO_DIR.exists():
        return JSONResponse({"outputs": []})

    # BroadcastOS names files "{show_id}_{story_id}_broadcastos_{timestamp}.mp4"
    # — story_id is always a 12-char hex hash (agents/trend_detection_agent.py's
    # md5(url)[:12] or a uuid4 fallback), so pull it out from the middle of the
    # filename rather than using the whole stem, which would never match the
    # per-story metadata/stats sidecar files. Falls back to the whole stem for
    # any older/differently-named video (e.g. the legacy avatar_agent pipeline).
    _STORY_ID_RE = re.compile(r"_([0-9a-f]{12})_broadcastos_")

    for video_file in sorted(VIDEO_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        m = _STORY_ID_RE.search(video_file.stem)
        story_id = m.group(1) if m else video_file.stem

        # Find matching thumbnail (any show suffix)
        thumb_files = list(THUMB_DIR.glob(f"thumb_{story_id}_*.jpg")) if THUMB_DIR.exists() else []
        thumb_name  = thumb_files[0].name if thumb_files else None

        # Derive show name from thumbnail filename
        show_label = ""
        if thumb_name:
            parts = thumb_name.replace(".jpg", "").split("_")
            show_label = parts[-1].replace("_", " ").title() if len(parts) > 2 else ""

        # Load metadata if available
        meta = {}
        meta_files = list(META_DIR.glob(f"{story_id}_metadata.json")) if META_DIR.exists() else []
        if meta_files:
            try:
                with open(meta_files[0], encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass

        # Story statistics — not shown in the broadcast video itself, only
        # here for admin review (broadcast_os.orchestrator saves this
        # sidecar file per broadcast; see _save_stats_for_dashboard).
        stats = {}
        stats_files = list(META_DIR.glob(f"{story_id}_stats.json")) if META_DIR.exists() else []
        if stats_files:
            try:
                with open(stats_files[0], encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                pass

        size_mb = round(video_file.stat().st_size / 1_048_576, 1)
        mtime   = datetime.fromtimestamp(video_file.stat().st_mtime).strftime("%d %b %Y  %H:%M")

        items.append({
            "story_id":   story_id,
            "video":      video_file.name,
            "thumbnail":  thumb_name,
            "show":       show_label,
            "title":      meta.get("title", "Untitled Broadcast"),
            "tags":       meta.get("tags", [])[:5],
            "size_mb":    size_mb,
            "created_at": mtime,
            "stats":      stats,
        })

    return JSONResponse({"outputs": items})


# ── Health endpoint ────────────────────────────────────────
@app.get("/health")
async def health_check():
    from datetime import datetime

    from core.database import get_stats

    # Check DB
    try:
        stats = get_stats()
        db_ok = True
    except Exception:
        stats = {}
        db_ok = False

    # Check last broadcast time
    last_broadcast          = None
    last_broadcast_age_mins = None
    if broadcast_history and current_run.get("completed_at"):
        try:
            last_dt = datetime.fromisoformat(
                current_run["completed_at"]
            )
            last_broadcast = last_dt.strftime("%d %b %Y %H:%M PKT")
            diff = datetime.now() - last_dt.replace(tzinfo=None)
            last_broadcast_age_mins = int(diff.total_seconds() / 60)
        except Exception:
            pass

    # Check API keys
    api_status = {
        "groq":    bool(config.api.groq_api_key),
        "heygen":  bool(config.api.heygen_api_key),
        "youtube": bool(config.api.youtube_api_key),
    }

    # Overall system status
    critical_ok = db_ok and api_status["groq"]
    status      = "healthy" if critical_ok else "degraded"

    return JSONResponse({
        "status":        status,
        "channel":       config.channel.name,
        "timestamp":     datetime.now(UTC).isoformat(),
        "timestamp_pkt": datetime.now().strftime("%d %b %Y %H:%M PKT"),
        "pipeline": {
            "is_running":              is_running,
            "last_broadcast":          last_broadcast,
            "last_broadcast_age_mins": last_broadcast_age_mins,
            "total_broadcasts":        len(broadcast_history),
        },
        "database": {
            "ok":        db_ok,
            "new":       stats.get("new",       0),
            "broadcast": stats.get("broadcast", 0),
            "rejected":  stats.get("rejected",  0),
        },
        "api_keys": api_status,
        "shows":    len(config.scheduler.shows),
        "version":  "1.0.0",
    }, status_code=200 if status == "healthy" else 503)


# ── Corrections endpoint ───────────────────────────────────
@app.post("/correct/{story_id}")
async def flag_correction(story_id: str, note: str = ""):
    """Flag a broadcast story as incorrect — drafts a public correction
    via agents/corrections_agent.py, publishes it to the video
    (description + comment) if it's a real upload, and records it in
    the `corrections` table regardless."""
    if not note:
        return JSONResponse(
            {"error": "note is required — describe what was wrong"},
            status_code=400,
        )
    try:
        from agents.corrections_agent import run_corrections_agent
        result = run_corrections_agent(story_id, note)
        _log(
            f"Correction published for story {story_id}: "
            f"{result['correction_text'][:80]}",
            "warning",
        )
        return JSONResponse({"status": "published" if result["published"] else "recorded", **result})

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
