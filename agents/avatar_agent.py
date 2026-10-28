"""agents/avatar_agent.py — Avatar Agent (fallback video pipeline).

Deterministic pipeline (no LLM reasoning involved) — per
https://agentfactory.panaversity.org/'s Gate 1a, generating narration
via edge-tts and assembling a fixed sequence of ffmpeg calls is "one
exact step that is the same every time," not agent work. Used only
when video_composer.run_video_composer (the primary pipeline) fails;
see orchestrator.py's fallback in run_broadcast(). Delegates TTS to
voice_agent.text_to_speech instead of keeping its own copy of the
lock/retry logic.
"""
import os
import uuid
from pathlib import Path

from loguru import logger

from agents.voice_agent import DEFAULT_VOICE, text_to_speech
from config.settings import config
from core.models import AvatarVideo, VideoScript, VideoStatus
from video import video_editor
from video.heygen_client import heygen

# ── Edge-TTS voice ─────────────────────────────────────────
TTS_VOICE = os.getenv("EDGE_TTS_VOICE", DEFAULT_VOICE)


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
            text_to_speech(text, audio_path, voice=TTS_VOICE)
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
