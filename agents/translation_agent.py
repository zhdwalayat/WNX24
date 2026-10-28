"""agents/translation_agent.py — Translation Agent.

A genuine agent per https://agentfactory.panaversity.org/'s split:
translation requires reasoning about meaning, idiom, and register in a
target language — not a fixed lookup or template — so this reasons via
the LLM rather than a deterministic dictionary/library swap.
"""
from loguru import logger

from agents.script_writer_agent import _call_groq_with_retry
from core.models import VideoScript

_SYSTEM_PROMPT = (
    "You are a professional broadcast translator for WNX24. Translate "
    "the given news script into the target language, preserving "
    "meaning, tone and news register exactly — never add, remove, or "
    "soften facts. Keep numbers, names and places accurate. Output "
    "only the translated text, with no preamble or notes."
)


def translate_text(text: str, target_language: str) -> str:
    """Translate `text` into `target_language`. Returns the original
    text unchanged if the input is empty."""
    if not text.strip():
        return text

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Target language: {target_language}\n\nText:\n{text}"},
    ]
    # Translations can legitimately be short — floor scales with the
    # source text instead of assuming broadcast-script length.
    min_words = max(1, len(text.split()) // 2)
    return _call_groq_with_retry(
        messages, max_tokens=max(300, len(text) * 2), temperature=0.2, min_words=min_words,
    )


def translate_script(script: VideoScript, target_language: str) -> VideoScript:
    """Return a new VideoScript with every narrated section translated
    into `target_language`. `story_id`/`word_count`/`duration_secs`
    metadata is carried over from the original (word count is not
    recomputed — translated languages don't map 1:1 to English wpm)."""
    logger.info(f"Starting Translation Agent — {script.story_id} -> {target_language}")

    translated = script.model_copy(update={
        "hook":           translate_text(script.hook, target_language),
        "what_happened":  translate_text(script.what_happened, target_language),
        "why_it_matters": translate_text(script.why_it_matters, target_language),
        "expert_insight": translate_text(script.expert_insight, target_language),
        "forward_look":   translate_text(script.forward_look, target_language),
        "cta":            translate_text(script.cta, target_language),
    })
    translated.full_text = " ".join(filter(None, [
        translated.hook, translated.what_happened, translated.why_it_matters,
        translated.expert_insight, translated.forward_look, translated.cta,
    ])).strip()

    logger.info("Translation Agent complete")
    return translated


def run_translation_agent(script: VideoScript, target_language: str) -> VideoScript:
    """Main entry point."""
    return translate_script(script, target_language)
