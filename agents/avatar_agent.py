import os
import asyncio
import uuid
from pathlib import Path
from loguru import logger

from core.models import VideoScript, AvatarVideo, VideoStatus
from config.settings import config
from video.heygen_client import heygen
from video import video_editor

# ── Edge-TTS voice ─────────────────────────────────────────
TTS_VOICE  = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")   # free Microsoft neural voice


# ── Step 1: Text → speech (edge-tts, fully free) ──────────
async def _tts_async(text: str, out_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(out_path)


_TTS_LOCK_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "output", "tts.lock")


_TTS_LOCK_STALE_SECS = 180   # a held lock older than this is from a dead process


def _acquire_tts_lock(timeout: int = 120) -> None:
    """Busy-wait until no other process holds the TTS lock file."""
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            fd = os.open(_TTS_LOCK_FILE,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return                          # lock acquired
        except FileExistsError:
            try:
                age = _time.time() - os.path.getmtime(_TTS_LOCK_FILE)
                if age > _TTS_LOCK_STALE_SECS:
                    logger.warning(
                        f"Removing stale TTS lock (age {age:.0f}s)"
                    )
                    os.remove(_TTS_LOCK_FILE)
                    continue
            except FileNotFoundError:
                continue                   # lock released between checks
            _time.sleep(0.5)               # another process holds it
    raise RuntimeError("Could not acquire TTS lock after timeout")


def _release_tts_lock() -> None:
    try:
        os.remove(_TTS_LOCK_FILE)
    except FileNotFoundError:
        pass


def _text_to_speech(text: str, out_path: str, retries: int = 3) -> None:
    import time as _time

    _acquire_tts_lock()
    try:
        for attempt in range(1, retries + 1):
            try:
                asyncio.run(_tts_async(text, out_path))
                if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                    logger.info(f"TTS saved → {out_path} ({os.path.getsize(out_path)} bytes)")
                    return
                logger.warning(f"TTS attempt {attempt}: empty file, retrying...")
            except Exception as e:
                logger.warning(f"TTS attempt {attempt} error: {e}")
            _time.sleep(3 * attempt)
        raise RuntimeError(f"TTS failed after {retries} attempts")
    finally:
        _release_tts_lock()


# ── Main: generate local video ─────────────────────────────
def generate_local_video(script: VideoScript) -> AvatarVideo:
    job_id     = script.story_id or uuid.uuid4().hex[:12]
    output_dir = Path(config.output_dir) / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)

    video_path = str(output_dir / f"{job_id}.mp4")

    # 1. TTS — one narration clip per script section
    section_audio = {}
    try:
        for section in video_editor.SECTION_ORDER:
            text = (video_editor.SECTION_TEXT[section](script) or "").strip()
            if not text:
                continue
            audio_path = str(output_dir / f"{job_id}_{section}.mp3")
            logger.info(f"TTS [{section}] — {len(text)} chars via edge-tts ({TTS_VOICE})")
            _text_to_speech(text, audio_path)
            section_audio[section] = audio_path
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return AvatarVideo(script=script, status=VideoStatus.FAILED)

    if not section_audio:
        logger.error("No narration audio generated — script sections all empty")
        return AvatarVideo(script=script, status=VideoStatus.FAILED)

    # 2. Assemble multi-scene broadcast video via FFmpeg
    logger.info("Assembling multi-scene broadcast video...")
    try:
        duration = video_editor.render_broadcast(script, section_audio, video_path)
    except Exception as e:
        logger.error(f"Video build failed: {e}")
        return AvatarVideo(script=script, status=VideoStatus.FAILED)
    finally:
        for audio_path in section_audio.values():
            try:
                os.remove(audio_path)
            except Exception:
                pass

    return AvatarVideo(
        script        = script,
        local_path    = video_path,
        heygen_job_id = job_id,
        duration_secs = duration,
        status        = VideoStatus.RENDERED,
    )


# ── Main agent function ────────────────────────────────────
# Set to True once testing is complete to use the paid HeyGen avatar.
USE_HEYGEN = False


def run_avatar_agent(script: VideoScript) -> AvatarVideo:
    if USE_HEYGEN and config.api.heygen_api_key:
        logger.info("Starting Avatar Agent — WNX24 (HeyGen)")
        job_id, local_path = heygen.generate(script.full_text, script.duration_secs)

        if local_path and os.path.exists(local_path):
            logger.info(
                f"Avatar Agent complete — "
                f"Status: {VideoStatus.RENDERED} | "
                f"Path: {local_path}"
            )
            return AvatarVideo(
                script        = script,
                local_path    = local_path,
                heygen_job_id = job_id,
                duration_secs = script.duration_secs,
                status        = VideoStatus.RENDERED,
            )

        logger.warning("HeyGen video generation failed — falling back to local TTS + video")

    logger.info("Starting Avatar Agent — WNX24 (local TTS + video)")

    video = generate_local_video(script)

    logger.info(
        f"Avatar Agent complete — "
        f"Status: {video.status} | "
        f"Path: {video.local_path}"
    )
    return video
