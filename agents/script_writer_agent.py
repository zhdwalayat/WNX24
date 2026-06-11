import os
import time
from typing import List
from datetime import datetime, timezone, timedelta
from loguru import logger
from groq import Groq

from core.models import VerifiedStory, VideoScript
from config.settings import config, ShowConfig


def is_developing_story(story: VerifiedStory, hours: int = 2) -> bool:
    """True if the story was published within the last `hours` hours."""
    try:
        pub = story.raw.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return pub >= datetime.now(timezone.utc) - timedelta(hours=hours)
    except Exception:
        return False

client = Groq(api_key=config.api.groq_api_key)
MODEL  = "llama-3.3-70b-versatile"  # mixtral decommissioned; 70b versatile is Groq's recommended replacement

def _call_groq_with_retry(
    messages: list,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    max_retries: int = 3,
) -> str:
    """
    Call Groq API with exponential backoff retry.
    Retries on rate limit, timeout, or empty response.
    """
    delays = [5, 15, 30]   # seconds between retries

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model       = MODEL,
                messages    = messages,
                temperature = temperature,
                max_tokens  = max_tokens,
            )
            content = response.choices[0].message.content

            # Reject if response is too short
            if not content or len(content.split()) < 30:
                raise ValueError(
                    f"Response too short: {len(content.split())} words"
                )

            return content

        except Exception as e:
            if attempt < max_retries - 1:
                wait = delays[attempt]
                logger.warning(
                    f"Groq API attempt {attempt + 1} failed: {e} "
                    f"— retrying in {wait}s"
                )
                time.sleep(wait)
            else:
                logger.error(
                    f"Groq API failed after {max_retries} attempts: {e}"
                )
                raise

    raise RuntimeError("Groq API failed — all retries exhausted")


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
        "explainer":     "Simple, clear, educational. Explain one concept "
                         "so a 15-year-old understands it completely. "
                         "Use analogies. Avoid all jargon.",
        "week_review":   "Reflective and comprehensive. Summarise the week "
                         "with authority. Connect stories to show the bigger "
                         "picture of what the week meant for the world.",
    }
    tone = tone_map.get(show.id, "Professional, balanced, and clear.")

    return f"""
You are a senior broadcast journalist at MERIDIAN NEWS NETWORK (MNN).
Write a complete, professional broadcast script for Aria to read on air.

SHOW:      {show.name}
TONE:      {tone}
ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"

MANDATORY WORD COUNT: {word_target} words minimum.
Count every word before submitting. Scripts under {word_target - 20} words
will be rejected and rebroadcast will fail.

STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}
URL:     {story.raw.url}

WRITE EACH SECTION IN FULL — DO NOT SUMMARISE:

[HOOK]
REQUIREMENT: 1 punchy sentence, maximum 15 words.
Grab attention immediately. Make the audience stop scrolling.

[OPEN]
REQUIREMENT: Use exactly this line:
"{anchor_open}"

[WHAT_HAPPENED]
REQUIREMENT: Minimum 60 words. At least 5 sentences.
— Sentence 1: Begin with "According to {story.raw.source_name}," then
  state what happened, who was involved, where and when.
— Sentence 2: Background — who is this person, company or institution.
— Sentence 3: Specific details of the event — what exactly occurred.
— Sentence 4: Immediate response — what happened next, reactions.
— Sentence 5: Current status — where things stand right now.
DO NOT summarise. Write every sentence in full.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 40 words. At least 3 sentences.
— Explain the real-world impact on ordinary people.
— Connect specifically to Pakistan, the Gulf region, or South Asia.
  Use this exact format: "For Pakistan and the region, this means..."
— State the broader global significance.

[EXPERT_INSIGHT]
REQUIREMENT: Minimum 40 words. At least 3 sentences.
— Report what analysts, experts, and observers are saying.
— Use phrases like: "Analysts suggest...", "Experts warn...",
  "According to observers...", "Policy experts note..."
— Aria does NOT have personal opinions. She reports what others say.
— What should people watch for in the coming days or weeks?

[SIGN_OFF]
REQUIREMENT: Use exactly these two lines:
"{config.anchor.sign_off}"
"This was {show.name} on MNN — Meridian News Network."

STRICT STYLE RULES:
— British English spelling throughout.
— Maximum 20 words per sentence.
— No jargon. No clickbait. No sensationalism.
— Speak to everyone — student, investor, farmer, professional.
— Never say "In conclusion" or "To summarise".
— Never use graphic or distressing language.
— For conflict stories: report casualties factually using passive
  language — "lives were lost" not "people were killed".
— For health stories: never cause alarm. Always note viewers should
  consult medical professionals. Never give specific medical advice.
— Total word count MUST be {word_target} words or more.
"""

# ── Explainer prompt builder ───────────────────────────────
def build_explainer_prompt(story: VerifiedStory, show: ShowConfig) -> str:
    anchor_open = config.anchor.opens.get("explainer")
    word_target = max(200, (show.video_duration // 60) * config.content.words_per_minute)

    return f"""
You are a senior broadcast journalist at MNN — Meridian News Network.
Write an EXPLAINER script for Aria. This is not a news bulletin.
This is an educational segment that explains ONE story or concept simply.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum

STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}

EXPLAINER FORMAT — write exactly these sections:

[HOOK]
One question that makes the audience think.
Example: "Why does a decision in Washington affect your grocery bill in Karachi?"
Maximum 20 words.

[OPEN]
Use exactly: "{anchor_open}"

[THE_BASICS]
REQUIREMENT: Minimum 50 words.
Explain the background simply. Assume the audience knows nothing.
Answer: What is this? Who is involved? Why does it exist?
Use simple analogies. No jargon.

[WHAT_HAPPENED]
REQUIREMENT: Minimum 50 words.
According to {story.raw.source_name}, what specifically happened?
Keep it simple and factual. One development at a time.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 50 words.
Why should a person in Pakistan, the Gulf, or Asia care?
Use: "For Pakistan and the region, this means..."
Connect to daily life — jobs, prices, safety, technology.

[THE_BIG_PICTURE]
REQUIREMENT: Minimum 40 words.
What does this tell us about where the world is heading?
Analysts suggest... / Experts note... / Observers say...
What should the audience watch for next?

[SIGN_OFF]
"{config.anchor.sign_off}"
"This was MNN Explainer on Meridian News Network."

RULES:
— Use simple language. Short sentences. Maximum 15 words each.
— Use analogies: "Think of it like..."
— Never use jargon without immediately explaining it.
— British English spelling.
— Total MUST be {word_target} words or more.
"""

# ── Section label prefixes the LLM sometimes inserts ──────
_SECTION_PREFIXES = (
    "hook:", "open:", "what happened:", "what_happened:",
    "why it matters:", "why_it_matters:", "expert insight:",
    "expert_insight:", "sign off:", "sign_off:",
    "the basics:", "the_basics:", "the big picture:", "the_big_picture:",
)

def _strip_label(text: str) -> str:
    """Remove leading LLM-inserted section labels like 'Hook: ' from content."""
    t = text.strip()
    lower = t.lower()
    for prefix in _SECTION_PREFIXES:
        if lower.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    return t


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
        if   "[HOOK]"           in line: current = "hook";           continue
        elif "[OPEN]"           in line: current = "open";           continue
        elif "[WHAT_HAPPENED]"  in line: current = "what_happened";  continue
        elif "[WHY_IT_MATTERS]" in line: current = "why_it_matters"; continue
        elif "[EXPERT_INSIGHT]" in line: current = "expert_insight"; continue
        elif "[SIGN_OFF]"       in line: current = "sign_off";       continue
        elif "[THE_BASICS]"     in line: current = "what_happened";  continue
        elif "[THE_BIG_PICTURE]"in line: current = "expert_insight"; continue
        if current:
            sections[current] += line + " "

    # Strip any "Hook:", "What Happened:" etc. labels the LLM added
    for key in sections:
        sections[key] = _strip_label(sections[key])

    # Build full script text for avatar TTS
    full_text = " ".join(filter(None, [
        sections["hook"],
        sections["open"],
        sections["what_happened"],
        sections["why_it_matters"],
        sections["expert_insight"],
        sections["sign_off"],
    ])).strip()

    # ── Fallback: if section markers were ignored, use raw text directly ──
    if not full_text:
        logger.warning("parse_script: no section markers found — using raw text")
        full_text = raw.strip()
        lines     = [l.strip() for l in raw.splitlines() if l.strip()]

        # First non-empty line → hook (strip any label prefix)
        hook = _strip_label(lines[0])[:120] if lines else "Breaking News"

        # Split remaining lines roughly into thirds for the three body sections
        body_lines = lines[1:] if len(lines) > 1 else []
        n = len(body_lines)
        third = max(1, n // 3)
        what_happened  = " ".join(body_lines[:third])
        why_it_matters = " ".join(body_lines[third:2*third])
        expert_insight = " ".join(body_lines[2*third:])

        word_count    = len(full_text.split())
        duration_secs = int((word_count / config.content.words_per_minute) * 60)
        return VideoScript(
            story_id       = story_id,
            hook           = hook,
            what_happened  = what_happened,
            why_it_matters = why_it_matters,
            expert_insight = expert_insight,
            cta            = "",
            full_text      = full_text,
            word_count     = word_count,
            duration_secs  = duration_secs,
        )

    word_count    = len(full_text.split())
    duration_secs = int((word_count / config.content.words_per_minute) * 60)

    return VideoScript(
        story_id        = story_id,
        hook            = sections["hook"],
        what_happened   = sections["what_happened"],
        why_it_matters  = sections["why_it_matters"],
        expert_insight  = sections["expert_insight"],
        cta             = sections["sign_off"],
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

    raw_script = _call_groq_with_retry(
            messages=[
                {
                    "role":    "system",
                    "content": (
                        "You are a senior broadcast journalist at "
                        "Meridian News Network. You write clean, "
                        "authoritative, globally inclusive scripts. "
                        "CRITICAL: You MUST output each section using "
                        "the EXACT markers on their own line: "
                        "[HOOK], [OPEN], [WHAT_HAPPENED], "
                        "[WHY_IT_MATTERS], [EXPERT_INSIGHT], [SIGN_OFF]. "
                        "Write the section content directly after the "
                        "marker — do NOT add the marker name as a label "
                        "before the content (do NOT write 'Hook: ...')."
                    )
                },
                {
                    "role":    "user",
                    "content": prompt
                }
            ],
            max_tokens  = 2000,
            temperature = 0.7,
        )
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
        "explainer":     "Simple, clear, educational. Explain one concept "
                         "so a 15-year-old understands it completely. "
                         "Use analogies. Avoid all jargon.",
        "week_review":   "Reflective and comprehensive. Summarise the week "
                         "with authority. Connect stories to show the bigger "
                         "picture of what the week meant for the world.",
    }
    tone = tone_map.get(show.id, "Professional, balanced, and clear.")

    prompt = f"""
You are a senior broadcast journalist at MERIDIAN NEWS NETWORK (MNN).
Write a {show.name} script covering {len(stories)} stories.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum ({show.video_duration} seconds)

{stories_text}

FORMAT:

[HOOK]
Single powerful opening line covering today's biggest theme.
Maximum 15 words.

[OPEN]
Use exactly: "{anchor_open}"

[WHAT_HAPPENED]
Cover each story in order.
— Begin each story with "According to [source name],"
— 3-4 sentences per story.
— Use "Meanwhile..." or "Also today..." to transition between stories.

[WHY_IT_MATTERS]
Overall significance of today's stories.
Include at least one connection to Pakistan, the Gulf, or South Asia:
"For Pakistan and the region, this means..."
3-4 sentences.

[EXPERT_INSIGHT]
What analysts and observers are saying about today's developments.
Use: "Analysts suggest..." / "Experts warn..." / "Observers note..."
Aria reports — she does not give personal opinions.
2-3 sentences.

[SIGN_OFF]
"{config.anchor.sign_off}"
"This was {show.name} on MNN — Meridian News Network."

RULES:
— {word_target} words minimum.
— British spelling.
— No jargon. No clickbait. No graphic language.
— Maximum 20 words per sentence.
"""

    raw_script = _call_groq_with_retry(
            messages=[
                {
                    "role":    "system",
                    "content": (
                        "You are a senior broadcast journalist at "
                        "Meridian News Network. "
                        "CRITICAL: You MUST output each section using "
                        "the EXACT markers on their own line: "
                        "[HOOK], [OPEN], [WHAT_HAPPENED], "
                        "[WHY_IT_MATTERS], [EXPERT_INSIGHT], [SIGN_OFF]. "
                        "Write content directly after each marker — "
                        "do NOT add the marker name as a label "
                        "before the content."
                    )
                },
                {
                    "role":    "user",
                    "content": prompt
                }
            ],
            max_tokens  = 3000,
            temperature = 0.7,
        )
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
        raise ValueError("GROQ_API_KEY missing from .env")

    # Use dedicated explainer prompt
    if show.id == "explainer":
        prompt = build_explainer_prompt(stories[0], show)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior broadcast journalist at MNN. "
                    "You specialise in explaining complex stories "
                    "simply and clearly. You never use jargon. "
                    "You always follow the exact format requested."
                )
            },
            {"role": "user", "content": prompt}
        ]
        raw_script = _call_groq_with_retry(
            messages    = messages,
            max_tokens  = 2000,
            temperature = 0.6,
        )
        return parse_script(raw_script, stories[0].raw.id, show)

    # Use week review prompt
    if show.id == "week_review":
        return write_multi_script(
            stories[:show.story_count], show
        )

    # Standard flow
    if show.story_count == 1 or len(stories) == 1:
        return write_script(stories[0], show)
    else:
        return write_multi_script(
            stories[:show.story_count], show
        )
