# agents/voice_agent.py
"""Text-to-speech agent — replaces the HeyGen avatar voice track.

Generates one narration MP3 per script section using Microsoft's free
edge-tts neural voices, with an espeak fallback for when edge-tts
(which needs network access to Microsoft's service) is unavailable.
"""
import asyncio
import os
import re
import subprocess
import time
from datetime import datetime

import imageio_ffmpeg
from loguru import logger

from config.settings import config
from core.models import VideoScript

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

DEFAULT_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")

# ── Script section -> narration text ───────────────────────
SECTION_ORDER: list[str] = [
    "hook", "what_happened", "why_it_matters", "expert_insight",
]
SECTION_TEXT: dict[str, callable] = {
    "hook":           lambda s: s.hook,
    "what_happened":  lambda s: s.what_happened,
    "why_it_matters": lambda s: s.why_it_matters,
    "expert_insight": lambda s: s.expert_insight,
}

# ── Cross-process TTS lock ──────────────────────────────────
# edge-tts occasionally misbehaves when hit concurrently from the same
# host; a simple file lock keeps calls serialized. Shared lock path with
# avatar_agent.py so the two pipelines never collide if run together.
_TTS_LOCK_FILE = os.path.join(config.output_dir, "tts.lock")
_TTS_LOCK_STALE_SECS = 180


def _acquire_tts_lock(timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(_TTS_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(_TTS_LOCK_FILE)
                if age > _TTS_LOCK_STALE_SECS:
                    logger.warning(f"Removing stale TTS lock (age {age:.0f}s)")
                    os.remove(_TTS_LOCK_FILE)
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.5)
    raise RuntimeError("Could not acquire TTS lock after timeout")


def _release_tts_lock() -> None:
    try:
        os.remove(_TTS_LOCK_FILE)
    except FileNotFoundError:
        pass


# ── Step 1: text -> speech ──────────────────────────────────
async def _edge_tts_async(text: str, output_path: str, voice: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def _espeak_fallback(text: str, output_path: str) -> bool:
    """Last-resort offline TTS via the `espeak` CLI, if installed.
    Renders to WAV then transcodes to MP3 with ffmpeg. Returns True on
    success, False if espeak isn't available or the render failed."""
    wav_path = output_path.rsplit(".", 1)[0] + "_espeak.wav"
    try:
        result = subprocess.run(
            ["espeak", "-s", "150", "-w", wav_path, text],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0 or not os.path.exists(wav_path):
            logger.error(f"espeak failed: {result.stderr.strip()[:300]}")
            return False

        convert = subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-i", wav_path,
             "-ar", "44100", "-ac", "2", "-b:a", "192k", output_path],
            capture_output=True, text=True, timeout=60,
        )
        return convert.returncode == 0 and os.path.exists(output_path)
    except FileNotFoundError:
        logger.error("espeak not found on PATH — offline fallback unavailable")
        return False
    except Exception as e:
        logger.error(f"espeak fallback error: {e}")
        return False
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def text_to_speech(text: str, output_path: str, voice: str = DEFAULT_VOICE) -> str:
    """Convert `text` to an MP3 narration file at `output_path`.
    Retries edge-tts once, then falls back to espeak if still failing."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    _acquire_tts_lock()
    try:
        last_error: Exception | None = None
        for attempt in range(1, 3):    # try edge-tts twice total
            try:
                asyncio.run(_edge_tts_async(text, output_path, voice))
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                    logger.info(
                        f"TTS saved → {output_path} "
                        f"({os.path.getsize(output_path)} bytes, voice={voice})"
                    )
                    return output_path
                logger.warning(f"edge-tts attempt {attempt}: empty/short output, retrying...")
            except Exception as e:
                last_error = e
                logger.warning(f"edge-tts attempt {attempt} failed: {e}")
            time.sleep(2 * attempt)

        logger.error(f"edge-tts failed after retries ({last_error}) — trying espeak fallback")
        if _espeak_fallback(text, output_path):
            logger.info(f"espeak fallback saved → {output_path}")
            return output_path

        raise RuntimeError(f"TTS failed for all engines: {last_error}")
    finally:
        _release_tts_lock()


async def text_to_speech_async(text: str, output_path: str, voice: str = DEFAULT_VOICE) -> str:
    """Async wrapper kept for callers that already run inside an event loop."""
    return await asyncio.to_thread(text_to_speech, text, output_path, voice)


# ── Step 2: audio duration ──────────────────────────────────
def get_audio_duration(audio_path: str) -> float:
    """Return media duration in seconds. Prefers `ffprobe` (fast, exact)
    and falls back to parsing `ffmpeg -i` stderr when ffprobe isn't on
    PATH — imageio_ffmpeg only bundles ffmpeg, not ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, ValueError):
        pass
    except Exception as e:
        logger.warning(f"ffprobe duration check failed: {e}")

    try:
        result = subprocess.run(
            [FFMPEG, "-i", audio_path], capture_output=True, text=True, timeout=30,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr)
        if m:
            h, mins, secs, cs = (int(g) for g in m.groups())
            return h * 3600 + mins * 60 + secs + cs / 100.0
    except Exception as e:
        logger.warning(f"ffmpeg duration check failed for {audio_path}: {e}")

    return 0.0


# ── Step 3: script -> narrated sections ─────────────────────
def script_to_sections(script: VideoScript, out_dir: str, voice: str = DEFAULT_VOICE) -> list[dict]:
    """Split a VideoScript into narrated sections, generating one audio
    file per non-empty section. Returns a list of
    {section, text, audio_path, duration} dicts."""
    os.makedirs(out_dir, exist_ok=True)
    sections: list[dict] = []

    for section in SECTION_ORDER:
        text = (SECTION_TEXT[section](script) or "").strip()
        if not text:
            continue

        audio_path = os.path.join(out_dir, f"{section}.mp3")
        logger.info(f"TTS [{section}] — {len(text)} chars via {voice}")
        try:
            text_to_speech(text, audio_path, voice)
        except Exception as e:
            logger.error(f"TTS failed for section '{section}': {e}")
            continue

        duration = get_audio_duration(audio_path)
        sections.append({
            "section":    section,
            "text":       text,
            "audio_path": audio_path,
            "duration":   duration,
        })

    return sections


# ── Main entry point ────────────────────────────────────────
def run_voice_agent(script: VideoScript, show_id: str) -> list[dict]:
    """Generate narration audio for every section of `script`.
    Returns a list of section dicts with audio paths + durations."""
    logger.info("Starting Voice Agent — WNX24 (edge-tts)")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir   = os.path.join(config.output_dir, "audio", f"{show_id}_{timestamp}")

    sections = script_to_sections(script, out_dir)

    total_duration = sum(s["duration"] for s in sections)
    logger.info(
        f"Voice Agent complete — {len(sections)} sections, "
        f"{total_duration:.1f}s total narration"
    )
    return sections
