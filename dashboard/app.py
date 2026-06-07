"""
dashboard/app.py — Meridian World News Control Room
Interactive dashboard with live pipeline execution.

Run:  uvicorn dashboard.app:app --reload --port 8000
Open: http://localhost:8000
"""

import os
import sys
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── Allow imports from project root ───────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import config

app = FastAPI(title="Meridian World News — Control Room")

# ── Static files & templates ───────────────────────────────
BASE      = Path(os.path.dirname(os.path.abspath(__file__)))
OUTPUT    = Path("output")
THUMB_DIR = OUTPUT / "thumbnails"
VIDEO_DIR = OUTPUT / "videos"
META_DIR  = OUTPUT / "metadata"

app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# ── In-memory state ────────────────────────────────────────
broadcast_log:     List[Dict]     = []
broadcast_history: List[Dict]     = []
current_run:       Dict[str, Any] = {}
is_running:        bool           = False


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

        url = await loop.run_in_executor(None, run_uploader_agent, package)

        if config.api.youtube_api_key:
            _log(f"         Uploaded — {url}", "success")
        else:
            _log("         Mock upload — add YOUTUBE_API_KEY for real upload", "warning")

        current_run["youtube_url"] = url
        current_run["title"]       = package.title or script.hook
        current_run["completed_at"] = datetime.now().isoformat()

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

    for video_file in sorted(VIDEO_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        story_id = video_file.stem          # e.g. 26c53171811e

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
                with open(meta_files[0]) as f:
                    meta = json.load(f)
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
        })

    return JSONResponse({"outputs": items})
