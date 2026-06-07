import os
from typing import List
from loguru import logger
from groq import Groq

from core.models import VerifiedStory, VideoScript
from config.settings import config, ShowConfig

client = Groq(api_key=config.api.groq_api_key)
MODEL  = "llama-3.3-70b-versatile"  # mixtral decommissioned; 70b versatile is Groq's recommended replacement

# ── Prompt builder ─────────────────────────────────────────
def build_prompt(
    story: VerifiedStory,
    show: ShowConfig
) -> str:

    word_target = max(
        150,
        (show.video_duration // 60) * config.content.words_per_minute
    )
    anchor_open = config.anchor.opens.get(show.anchor_open, "This is Meridian.")

    tone_map = {
        "world_affairs": "Diplomatic and measured. Present all sides fairly. No bias.",
        "economy":       "Data-driven and clear. Explain financial impact in simple terms.",
        "science":       "Curious and authoritative. Make science accessible to everyone.",
        "health":        "Calm and reassuring. Never alarmist. Always evidence-based.",
        "conflict":      "Sober and factual. Report casualties and facts. No glorification.",
        "breaking":      "Urgent and sharp. Every word counts. Get to the point immediately.",
        "deep_dive":     "Analytical and thorough. Give context others miss.",
        "cybersecurity": "Technical but accessible. Explain threats in plain English.",
        "primetime":     "Authoritative and compelling. This is the biggest story of the day.",
        "summary":       "Comprehensive but concise. Cover everything efficiently.",
    }
    tone = tone_map.get(show.id, "Professional, balanced, and clear.")

    return f"""
You are a senior broadcast journalist at MERIDIAN WORLD NEWS.
Your job is to write a complete, full-length broadcast script.

SHOW:      {show.name}
TONE:      {tone}
ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY WORD COUNT: {word_target} WORDS MINIMUM
You will be penalised for submitting under {word_target} words.
Count your words before submitting.
Thin scripts are not acceptable for broadcast.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}
URL:     {story.raw.url}

WRITE EACH SECTION IN FULL. DO NOT SUMMARISE.

[HOOK]
REQUIREMENT: 1 sentence, maximum 15 words.
Write a punchy opening that grabs attention immediately.
Example style: "A billionaire tech leader was attacked — and the world is watching."

[OPEN]
REQUIREMENT: Use exactly this line:
"{anchor_open}"

[WHAT_HAPPENED]
REQUIREMENT: Minimum 60 words. Write at least 5 sentences.
- Sentence 1: What happened, who was involved, where, when.
- Sentence 2: Background — who is this person or company.
- Sentence 3: Details of the event — what exactly occurred.
- Sentence 4: Immediate response — what happened next.
- Sentence 5: Current status — where things stand right now.
Do NOT summarise. Write every sentence in full.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 40 words. Write at least 3 sentences.
- Why does this affect ordinary people.
- Impact on technology, security, economy, or politics.
- Connect to Pakistan, Middle East, or Asia where relevant.

[EXPERT_INSIGHT]
REQUIREMENT: Minimum 40 words. Write at least 3 sentences.
- What does this signal about the industry or world.
- What should people expect in the next weeks or months.
- Aria's authoritative professional take — not opinion, analysis.

[SIGN_OFF]
REQUIREMENT: Use exactly these two lines:
"{config.anchor.sign_off}"
"This was {show.name} on Meridian World News."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL CHECK BEFORE SUBMITTING:
[ ] HOOK written
[ ] OPEN written
[ ] WHAT_HAPPENED written (60+ words)
[ ] WHY_IT_MATTERS written (40+ words)
[ ] EXPERT_INSIGHT written (40+ words)
[ ] SIGN_OFF written
[ ] Total word count: {word_target}+ words
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULES:
- Never sensational. Never clickbait.
- Always accurate to the source material.
- Speak to everyone — student, investor, farmer.
- British English spelling.
- No emojis. No hashtags.
- Never say "In conclusion" or "To summarise".
- Maximum 20 words per sentence.
"""

# ── Script parser ──────────────────────────────────────────
def parse_script(raw: str, story_id: str,
                 show: ShowConfig) -> VideoScript:

    sections = {
        "hook":           "",
        "open":           "",
        "what_happened":  "",
        "why_it_matters": "",
        "expert_insight": "",
        "sign_off":       "",
    }

    current = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if   "[HOOK]"           in line: current = "hook"
        elif "[OPEN]"           in line: current = "open"
        elif "[WHAT_HAPPENED]"  in line: current = "what_happened"
        elif "[WHY_IT_MATTERS]" in line: current = "why_it_matters"
        elif "[EXPERT_INSIGHT]" in line: current = "expert_insight"
        elif "[SIGN_OFF]"       in line: current = "sign_off"
        elif current:
            sections[current] += line + " "

    # Build full script text for avatar TTS
    full_text = " ".join([
        sections["hook"],
        sections["open"],
        sections["what_happened"],
        sections["why_it_matters"],
        sections["expert_insight"],
        sections["sign_off"],
    ]).strip()

    # ── Fallback: if section markers were ignored, use raw text directly ──
    if not full_text:
        logger.warning("parse_script: no section markers found — using raw text")
        full_text = raw.strip()
        lines     = [l.strip() for l in raw.splitlines() if l.strip()]
        hook      = lines[0][:120] if lines else "Breaking News"
        body      = " ".join(lines[1:]) if len(lines) > 1 else ""
        word_count    = len(full_text.split())
        duration_secs = int((word_count / config.content.words_per_minute) * 60)
        return VideoScript(
            story_id      = story_id,
            hook          = hook,
            what_happened = body[:300],
            why_it_matters= "",
            expert_insight= "",
            cta           = "",
            full_text     = full_text,
            word_count    = word_count,
            duration_secs = duration_secs,
        )

    word_count    = len(full_text.split())
    duration_secs = int((word_count / config.content.words_per_minute) * 60)

    return VideoScript(
        story_id        = story_id,
        hook            = sections["hook"].strip(),
        what_happened   = sections["what_happened"].strip(),
        why_it_matters  = sections["why_it_matters"].strip(),
        expert_insight  = sections["expert_insight"].strip(),
        cta             = sections["sign_off"].strip(),
        full_text       = full_text,
        word_count      = word_count,
        duration_secs   = duration_secs,
    )

# ── Single story script ────────────────────────────────────
def write_script(
    story: VerifiedStory,
    show: ShowConfig
) -> VideoScript:

    logger.info(f"Writing script for: {story.raw.title[:60]}")

    prompt = build_prompt(story, show)

    response = client.chat.completions.create(
        model    = MODEL,
        messages = [
            {
                "role":    "system",
                "content": (
                    "You are a senior broadcast journalist at "
                    "Meridian World News. You write clean, "
                    "authoritative, globally inclusive scripts. "
                    "Always follow the exact format requested."
                )
            },
            {
                "role":    "user",
                "content": prompt
            }
        ],
        temperature = 0.7,
        max_tokens  = 1500,
    )

    raw_script = response.choices[0].message.content
    script     = parse_script(raw_script, story.raw.id, show)

    if script.word_count < 50:
        raise ValueError(
            f"Script too short ({script.word_count} words) — "
            "LLM may have returned an empty or truncated response"
        )

    logger.info(
        f"Script ready — {script.word_count} words "
        f"| ~{script.duration_secs}s "
        f"| Show: {show.name}"
    )

    return script

# ── Multi-story script (bulletin / summary) ────────────────
def write_multi_script(
    stories: List[VerifiedStory],
    show: ShowConfig
) -> VideoScript:

    logger.info(
        f"Writing multi-story script "
        f"({len(stories)} stories) for {show.name}"
    )

    stories_text = ""
    for i, s in enumerate(stories, 1):
        stories_text += f"""
STORY {i}:
Title:   {s.raw.title}
Source:  {s.raw.source_name}
Summary: {s.raw.summary[:300]}
---"""

    word_target  = (show.video_duration // 60) * config.content.words_per_minute
    anchor_open  = config.anchor.opens.get(show.anchor_open, "This is Meridian.")

    tone_map = {
        "world_affairs": "Diplomatic and measured. Present all sides fairly. No bias.",
        "economy":       "Data-driven and clear. Explain financial impact in simple terms.",
        "science":       "Curious and authoritative. Make science accessible to everyone.",
        "health":        "Calm and reassuring. Never alarmist. Always evidence-based.",
        "conflict":      "Sober and factual. Report casualties and facts. No glorification.",
        "breaking":      "Urgent and sharp. Every word counts. Get to the point immediately.",
        "deep_dive":     "Analytical and thorough. Give context others miss.",
        "cybersecurity": "Technical but accessible. Explain threats in plain English.",
        "primetime":     "Authoritative and compelling. This is the biggest story of the day.",
        "summary":       "Comprehensive but concise. Cover everything efficiently.",
    }
    tone = tone_map.get(show.id, "Professional, balanced, and clear.")

    prompt = f"""
You are the head scriptwriter for MERIDIAN WORLD NEWS.
Write a {show.name} script covering {len(stories)} stories.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words ({show.video_duration} seconds)
TONE:      {tone}

{stories_text}

FORMAT:

[HOOK]
Single powerful opening line covering today's biggest theme.

[OPEN]
"{anchor_open}"

[WHAT_HAPPENED]
Cover each story in order. 2-3 sentences per story.
Use "Meanwhile..." or "Also today..." to transition.

[WHY_IT_MATTERS]
Overall significance of today's stories combined.
3-4 sentences.

[EXPERT_INSIGHT]
One sharp observation connecting all stories.
What is the bigger picture?

[SIGN_OFF]
"{config.anchor.sign_off}"
"This was {show.name} on Meridian World News."

RULES:
- Simple clear English. Anyone can understand.
- British spelling.
- No jargon. No clickbait.
- Maximum 20 words per sentence.
"""

    response = client.chat.completions.create(
        model    = MODEL,
        messages = [
            {
                "role":    "system",
                "content": (
                    "You are a senior broadcast journalist at "
                    "Meridian World News."
                )
            },
            {
                "role":    "user",
                "content": prompt
            }
        ],
        temperature = 0.7,
        max_tokens  = 2000,
    )

    raw_script = response.choices[0].message.content
    script     = parse_script(
        raw_script,
        stories[0].raw.id,
        show
    )

    if script.word_count < 50:
        raise ValueError(
            f"Script too short ({script.word_count} words) — "
            "LLM may have returned an empty or truncated response"
        )

    logger.info(
        f"Multi-script ready — {script.word_count} words "
        f"| ~{script.duration_secs}s"
    )

    return script

# ── Main agent function ────────────────────────────────────
def run_script_writer(
    stories: List[VerifiedStory],
    show: ShowConfig
) -> VideoScript:

    if not config.api.groq_api_key:
        raise ValueError(
            "Groq API key missing. "
            "Add GROQ_API_KEY to your .env file."
        )

    for attempt in range(1, 4):
        try:
            if show.story_count == 1 or len(stories) == 1:
                return write_script(stories[0], show)
            else:
                return write_multi_script(
                    stories[:show.story_count],
                    show
                )
        except ValueError as e:
            if attempt < 3:
                logger.warning(f"Script attempt {attempt} failed: {e} — retrying...")
            else:
                raise
