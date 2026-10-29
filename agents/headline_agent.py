"""agents/headline_agent.py — Headline Agent.

A genuine agent per https://agentfactory.panaversity.org/'s split:
writing a punchy, accurate headline from a variable, unpredictable
story needs LLM judgment, not a template.

Standalone and reusable (thumbnails, YouTube titles, alternate
headline variants) — it does NOT replace script_writer_agent's
internal [HOOK] generation, which is written in the context of the
full anchor script and stays there so that flow keeps working exactly
as tested. This agent is for callers that need *just* a headline
without generating a whole script.
"""
from loguru import logger

from agents.script_writer_agent import _call_groq_with_retry
from core.models import VerifiedStory

_SYSTEM_PROMPT = (
    "You are a broadcast headline writer for WNX24. Write one headline "
    "per request: factual, active voice, no clickbait, no banned words "
    "(claims, admits, slammed, bombshell, shocking). Maximum 12 words. "
    "Output only the headline text, nothing else."
)


def generate_headline(story: VerifiedStory, max_words: int = 12) -> str:
    """Generate a single broadcast-style headline for `story`."""
    user_prompt = f"""STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}

Write ONE headline, maximum {max_words} words. Active voice, factual,
no adjectives, no speculation.
"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    # 60 tokens was consistently consumed entirely by gpt-oss-120b's hidden
    # reasoning (even at reasoning_effort="low"), leaving zero for the
    # actual headline and silently falling back to the raw scraped title
    # on every single call — see the same fix in agents/planning_agent.py.
    headline = _call_groq_with_retry(messages, max_tokens=150, temperature=0.5, min_words=2)
    return headline.strip().strip('"')


def generate_headline_variants(story: VerifiedStory, count: int = 3) -> list[str]:
    """Generate `count` distinct headline options for A/B choice
    (thumbnail testing, alternate title candidates)."""
    variants = []
    for _ in range(count):
        try:
            variants.append(generate_headline(story))
        except Exception as e:
            logger.warning(f"Headline variant generation failed: {e}")
    # de-dupe while preserving order
    seen, unique = set(), []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            unique.append(v)
    return unique


def run_headline_agent(story: VerifiedStory) -> str:
    """Main entry point."""
    logger.info(f"Starting Headline Agent — {story.raw.title[:50]}")
    headline = generate_headline(story)
    logger.info(f"Headline Agent complete — {headline}")
    return headline
