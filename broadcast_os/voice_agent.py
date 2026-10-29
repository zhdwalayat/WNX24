"""broadcast_os/voice_agent.py — BroadcastOS narration agent.

Thin async wrapper around the existing agents/voice_agent.py TTS pipeline
(edge-tts primary, espeak fallback, shared cross-process lock) — reused
rather than re-implemented so BroadcastOS and the avatar/video_composer
pipelines never fight over the TTS lock file or drift in retry behaviour.
"""
from __future__ import annotations

import asyncio
import os

from loguru import logger

from agents.voice_agent import DEFAULT_VOICE, get_audio_duration, text_to_speech
from config.settings import config
from core.models import VideoScript

# Section name -> VideoScript field. VideoScript has no `sign_off` field —
# the closing line lives in `cta` (see core/models.py).
# `sign_off` is intentionally excluded from SECTION_ORDER: the outro is a
# short silent bumper (see broadcast_os/scenes/outro_scene.py), not a
# narrated scene, so its text is never synthesised or spoken.
SECTION_TEXT: dict[str, callable] = {
    "intro": lambda s: s.hook,
    "what_happened": lambda s: s.what_happened,
    "why_it_matters": lambda s: s.why_it_matters,
    "expert_insight": lambda s: s.expert_insight,
}
SECTION_ORDER = ["intro", "what_happened", "why_it_matters", "expert_insight"]


class VoiceAgent:
    """Converts VideoScript sections to per-section narration audio."""

    def __init__(self, voice: str = DEFAULT_VOICE):
        self.voice = voice

    async def generate_all(self, script: VideoScript, story_id: str) -> dict[str, str]:
        out_dir = os.path.join(config.output_dir, "audio", story_id or "broadcast")
        os.makedirs(out_dir, exist_ok=True)

        results: dict[str, str] = {}
        for section in SECTION_ORDER:
            text = (SECTION_TEXT[section](script) or "").strip()
            if not text:
                continue
            audio_path = os.path.join(out_dir, f"{section}.mp3")
            try:
                await asyncio.to_thread(text_to_speech, text, audio_path, self.voice)
                results[section] = audio_path
            except Exception as e:
                logger.error(f"BroadcastOS VoiceAgent: TTS failed for '{section}': {e}")

        logger.info(f"BroadcastOS VoiceAgent complete — {len(results)}/{len(SECTION_ORDER)} sections narrated")
        return results

    @staticmethod
    def duration(audio_path: str) -> float:
        return get_audio_duration(audio_path)


async def run_voice_agent(script: VideoScript, story_id: str) -> dict[str, str]:
    return await VoiceAgent().generate_all(script, story_id)
