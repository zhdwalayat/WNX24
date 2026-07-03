
import os
import time
from typing import List, Tuple
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


# ── Shared helpers ─────────────────────────────────────────
def _word_target(show: ShowConfig) -> int:
    return max(150, (show.video_duration // 60) * config.content.words_per_minute)


def _anchor_open(show: ShowConfig) -> str:
    return config.anchor.opens.get(show.anchor_open, config.anchor.opens["default"])


def _sign_off_line(show: ShowConfig) -> str:
    """Closing line naming the show. Avoids 'This was WNX24 X on WNX24.'
    for shows whose name already includes the network name."""
    if "WNX24" in show.name:
        return f'"This was {show.name}."'
    return f'"This was {show.name} on WNX24."'


def _format_stories_block(stories: List[VerifiedStory]) -> str:
    blocks = []
    for i, s in enumerate(stories, 1):
        blocks.append(
            f"STORY {i}:\n"
            f"Title:   {s.raw.title}\n"
            f"Source:  {s.raw.source_name}\n"
            f"Summary: {s.raw.summary[:300]}\n"
            "---"
        )
    return "\n".join(blocks)


# ── Impact analysis (keyword scan, used by analysis shows) ──
_IMPACT_KEYWORDS = {
    "economic": [
        "economy", "economic", "market", "trade", "inflation", "prices",
        "gdp", "rupee", "dollar", "bank", "imf", "stocks", "jobs",
        "tariff", "investment", "exports", "imports",
    ],
    "political": [
        "government", "election", "president", "minister", "parliament",
        "policy", "diplomat", "sanctions", "treaty", "summit",
        "opposition", "vote",
    ],
    "security": [
        "military", "war", "attack", "troops", "strike", "conflict",
        "ceasefire", "missile", "border", "security", "defence",
    ],
    "social": [
        "health", "education", "community", "protest", "rights",
        "welfare", "population", "refugee", "workers",
    ],
    "environmental": [
        "climate", "flood", "earthquake", "emissions", "energy",
        "weather", "drought", "wildfire", "storm",
    ],
    "technological": [
        "ai", "technology", "cyber", "software", "data", "robot",
        "space", "satellite", "research", "internet",
    ],
}

_REGION_KEYWORDS = {
    "Pakistan":     ["pakistan", "karachi", "lahore", "islamabad", "rupee", "coas"],
    "Gulf":         ["gulf", "saudi", "uae", "qatar", "dubai", "riyadh"],
    "South Asia":   ["india", "bangladesh", "afghanistan", "sri lanka", "south asia"],
    "Europe":       ["europe", "eu ", "germany", "france", "uk", "britain"],
    "Middle East":  ["middle east", "israel", "iran", "gaza", "lebanon", "syria"],
    "Asia-Pacific": ["china", "japan", "korea", "asean", "asia-pacific"],
    "Africa":       ["africa", "nigeria", "kenya"],
    "Americas":     ["united states", "u.s.", "america", "canada", "mexico"],
}


def _analyse_story_impact(stories: List[VerifiedStory]) -> dict:
    """Keyword-scan story titles/summaries for likely impact areas and
    regions referenced, to give the LLM a genuine starting point."""
    combined = " ".join(
        f"{s.raw.title} {s.raw.summary}" for s in stories
    ).lower()

    impact_types = [
        category for category, words in _IMPACT_KEYWORDS.items()
        if any(word in combined for word in words)
    ]
    regions = [
        region for region, words in _REGION_KEYWORDS.items()
        if any(word in combined for word in words)
    ]

    return {
        "impact_types": impact_types or ["general"],
        "regions":      regions,
    }


# ── Universal newsroom rules shared by every builder ────────
_UNIVERSAL_RULES = """
NEWSROOM STANDARDS (BBC / Reuters / AP / Al Jazeera / NPR):
— Active voice always: "troops crossed", not "the border was crossed".
— Strong verbs over adjectives: "fell", not "dramatically collapsed".
— Specific numbers always: "47 killed", not "dozens killed".
— No jargon without immediate explanation.
— Attribute every factual claim once: "According to Reuters,"
  or "Al Jazeera reports" — name the source, never describe it
  ("the highly reliable Reuters agency" is WRONG).
— Never use: claims, admits, slammed, bombshell, shocking,
  dramatic, devastating. Use "says" or "acknowledges" instead
  of "admits", "criticised" instead of "slammed".
— Unconfirmed information must say "unconfirmed reports suggest".
— Never take sides on any political, social or religious issue.
  Present all perspectives factually. Aria has no personal
  opinions — she reports what others say.
— Expert voices only: "Analysts suggest", "Economists warn",
  "Scientists believe", "Health officials say", "Observers note".
  Never fabricate quotes or attribute opinions to unnamed sources.
— Allegations not verdicts: "accused of", never "guilty of".
  Use "alleged" for unproven claims. "He has been charged
  with" — never "the criminal".
— PEMRA compliance for Pakistan-related content:
  — Military and judiciary: facts plus attribution only —
    "According to ISPR, troops were deployed" — never imply
    criticism.
  — Religion: factual reporting only — report what officials
    or experts say, never an opinion on any religion.
  — Pakistan court cases: objective facts only — "The court
    heard arguments today" — never comment on the merits.
  — Banned organisations: report events, not their stated
    reasons — "An attack occurred in Peshawar".
  — Nothing that could incite violence, sectarian hatred or
    discrimination against any group in Pakistan.
— British spelling throughout."""


# ── Section label prefixes the LLM sometimes inserts ──────
_SECTION_PREFIXES = (
    "hook:", "open:", "what happened:", "what_happened:",
    "why it matters:", "why_it_matters:", "expert insight:",
    "expert_insight:", "sign off:", "sign_off:",
    "the basics:", "the_basics:", "the big picture:", "the_big_picture:",
    "forward look:", "forward_look:", "flash:",
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
        "forward_look":   "",
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
        elif "[FLASH]"          in line: current = "what_happened";  continue
        elif "[WHY_IT_MATTERS]" in line: current = "why_it_matters"; continue
        elif "[EXPERT_INSIGHT]" in line: current = "expert_insight"; continue
        elif "[FORWARD_LOOK]"   in line: current = "forward_look";   continue
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
        sections["forward_look"],
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
        forward_look    = sections["forward_look"],
        cta             = sections["sign_off"],
        full_text       = full_text,
        word_count      = word_count,
        duration_secs   = duration_secs,
    )


# ═════════════════════════════════════════════════════════════
# PROMPT BUILDERS
# ═════════════════════════════════════════════════════════════

_MARKER_INSTRUCTION = (
    "CRITICAL: You MUST output each section using the EXACT "
    "markers on their own line: {markers}. Write content "
    "directly after each marker — do NOT add the marker name "
    "as a label before the content (do NOT write 'Hook: ...')."
)


# ── Builder 1: breaking ──────────────────────────────────────
_BREAKING_SYSTEM = (
    "You are a breaking news wire reporter at WNX24. "
    "Your only job is facts. No analysis. No opinion. "
    "No adjectives. Maximum precision. Every word essential. "
    "If unsure, say unconfirmed. Never speculate. "
    + _MARKER_INSTRUCTION.format(markers="[HOOK], [OPEN], [FLASH], [SIGN_OFF]")
)

def build_breaking_prompt(stories: List[VerifiedStory], show: ShowConfig) -> Tuple[str, str]:
    story = stories[0]
    anchor_open = _anchor_open(show)

    user = f"""Write a BREAKING NEWS flash for Aria to read on air.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"

STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}
{_UNIVERSAL_RULES}

BREAKING NEWS RULES:
— MAXIMUM 80 words total. MAXIMUM 5 sentences.
— MAXIMUM 12 words per sentence.
— Cover only the 5 Ws: who, what, when, where, what happens next.
— ZERO analysis, ZERO speculation, ZERO adjectives.
— ONE source attribution maximum — "According to {story.raw.source_name},"

FORMAT:

[HOOK]
One urgent fact. Maximum 10 words.

[OPEN]
Use exactly: "{anchor_open}"

[FLASH]
3-4 sentences covering what, where, when, who and what happens
next. Facts only. One attribution.

[SIGN_OFF]
"WNX24 will bring you updates as they emerge."
"{config.anchor.sign_off}"
"""
    return _BREAKING_SYSTEM, user


# ── Builder 2: bulletin (morning/midday/evening/summary) ─────
_BULLETIN_SYSTEM = (
    "You are a BBC broadcast journalist at WNX24. "
    "Clear, factual, professional. Inverted pyramid always — "
    "most important fact first. Facts before analysis. "
    "One brief impact line per story — never deep analysis; "
    "save deep analysis for Deep Dive and Prime Time. "
    + _MARKER_INSTRUCTION.format(
        markers="[HOOK], [OPEN], [WHAT_HAPPENED], [WHY_IT_MATTERS], [SIGN_OFF]"
    )
)

def build_bulletin_prompt(stories: List[VerifiedStory], show: ShowConfig) -> Tuple[str, str]:
    word_target  = _word_target(show)
    anchor_open  = _anchor_open(show)
    stories_text = _format_stories_block(stories)

    user = f"""Write a {show.name} script covering {len(stories)} stories.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum ({show.video_duration} seconds)

{stories_text}
{_UNIVERSAL_RULES}

BULLETIN RULES:
— Inverted pyramid: lead with the most important story.
— ONE brief impact sentence per story — that is all.
— Use meaningful transitions that show connection between
  stories:
  "This connects directly to..."
  "As a consequence..."
  "Meanwhile the same pressure is driving..."
  Never use "In other news" or "Moving on".
— Genuine impact only — never force a regional angle.
  If a story affects Europe only, say Europe.
  If it affects global markets, say so.
  Be honest about who it actually affects.
— Maximum 20 words per sentence.

FORMAT:

[HOOK]
Today's biggest connecting theme. One line, maximum 15 words.

[OPEN]
Use exactly: "{anchor_open}"

[WHAT_HAPPENED]
Cover each story in turn, most important first.
— Begin each story with "According to [source name],"
— One fact-led paragraph per story plus its one impact sentence.
— Use the transition phrases above between stories.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 60 words. ONE unified analysis covering
ALL stories together — not separate sections per story.
WHO is affected beyond the headlines?
WHERE does today's combination of stories ripple?
WHEN do the combined impacts land — today, this week, longer term?
Genuine impact only — do not force a regional angle.

[SIGN_OFF]
"{config.anchor.sign_off}"
{_sign_off_line(show)}
"""
    return _BULLETIN_SYSTEM, user


# ── Builder 3: pakistan ───────────────────────────────────────
_PAKISTAN_SYSTEM = (
    "You are the WNX24 Pakistan correspondent. "
    "You know Pakistan deeply — its economy, politics and "
    "society. Cover the human story as well as the political "
    "story. Pakistan, Gulf or South Asia impact only when a "
    "genuine logical link exists — never force a Pakistan "
    "angle where none exists. Strict PEMRA compliance on "
    "military, judiciary and religion. "
    + _MARKER_INSTRUCTION.format(
        markers="[HOOK], [OPEN], [WHAT_HAPPENED], [WHY_IT_MATTERS], [SIGN_OFF]"
    )
)

def build_pakistan_prompt(stories: List[VerifiedStory], show: ShowConfig) -> Tuple[str, str]:
    word_target  = _word_target(show)
    anchor_open  = _anchor_open(show)
    stories_text = _format_stories_block(stories)

    user = f"""Write the {show.name} script covering {len(stories)} stories
for a Pakistani audience.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum ({show.video_duration} seconds)

{stories_text}
{_UNIVERSAL_RULES}

PAKISTAN DESK RULES:
— Pakistan stories lead always. Then regional stories that
  genuinely affect Pakistan.
— Mention Pakistan, the Gulf or South Asia ONLY where a
  genuine, logical connection exists. Do not invent one.
— Use natural local context where relevant: the rupee, the
  COAS, the State Bank, the Supreme Court, the provinces, the
  IMF programme, CPEC, the Line of Control.
— Cover the people affected by these stories, not just
  politicians.
— Maximum 20 words per sentence.

FORMAT:

[HOOK]
Pakistan-specific opening line. Maximum 15 words.

[OPEN]
Use exactly: "{anchor_open}"

[WHAT_HAPPENED]
Pakistan stories first, then regional stories affecting
Pakistan. Attribution for every claim.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 60 words. The real effect on Pakistani
citizens — economy, security, daily life. Only draw this
connection where it is genuine.

[SIGN_OFF]
"{config.anchor.sign_off}"
{_sign_off_line(show)}
"""
    return _PAKISTAN_SYSTEM, user


# ── Builder 4: analysis (deep_dive/primetime/week_review) ────
_ANALYSIS_SYSTEM = (
    "You are an investigative journalist at WNX24. "
    "Find the real story behind the story. Connect the dots "
    "others miss. Show what this means for real people — not "
    "just leaders. Treat the audience as intelligent adults. "
    + _MARKER_INSTRUCTION.format(
        markers="[HOOK], [OPEN], [WHAT_HAPPENED], [WHY_IT_MATTERS], "
                "[EXPERT_INSIGHT], [FORWARD_LOOK], [SIGN_OFF]"
    )
)

def build_analysis_prompt(stories: List[VerifiedStory], show: ShowConfig) -> Tuple[str, str]:
    word_target  = _word_target(show)
    anchor_open  = _anchor_open(show)
    stories_text = _format_stories_block(stories)

    impact = _analyse_story_impact(stories)
    if impact["regions"]:
        region_line = f"Regions referenced in the source material: {', '.join(impact['regions'])}."
    else:
        region_line = "No specific region beyond the story itself is indicated by the source material."
    impact_line = (
        f"DETECTED SIGNALS — likely impact areas: "
        f"{', '.join(impact['impact_types'])}. {region_line} "
        "Use this as a starting point only — verify genuine "
        "relevance before drawing any connection."
    )

    user = f"""Write a {show.name} script covering {len(stories)} stories.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum ({show.video_duration} seconds)

{stories_text}

{impact_line}
{_UNIVERSAL_RULES}

ANALYSIS RULES:
— Start with the bigger picture, not the event itself.
— WHY_IT_MATTERS must use the full 3-question framework:
  Q1 WHO is affected beyond the people in the headline?
     Which industries, governments, communities or workers?
  Q2 WHERE does this ripple geographically? Which regions are
     linked economically or politically?
  Q3 WHEN does the impact land? Immediate (today), short term
     (weeks), long term (months)?
— Genuine impact only. If it affects Europe only, say Europe.
  If it affects developing economies, say so. Never force a
  Pakistan or Gulf angle if none exists logically.
— EXPERT_INSIGHT must use institutional attribution:
  "IMF economists warn...", "UN officials say...",
  "Analysts at [named institution] suggest...". Never
  fabricate. Never name without basis.
— Cover the human story — the people affected, not just the
  leaders making decisions.
— FORWARD_LOOK: three SPECIFIC, measurable things to watch —
  not "watch the markets" but "watch whether the Pakistani
  rupee breaks the 290 barrier this week".
— Maximum 20 words per sentence.

FORMAT:

[HOOK]
The bigger story in one powerful line.

[OPEN]
Use exactly: "{anchor_open}"

[WHAT_HAPPENED]
Full facts. Attribution. Active voice.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 60 words. Answer the 3-question
framework — WHO, WHERE, WHEN. Genuine impact, never forced.

[EXPERT_INSIGHT]
REQUIREMENT: Minimum 50 words. What named institutions and
analysts say. Use: warn, suggest, believe, fear.

[FORWARD_LOOK]
"Three things to watch in the coming days:" then three
specific, measurable developments.

[SIGN_OFF]
"{config.anchor.sign_off}"
{_sign_off_line(show)}
"""
    return _ANALYSIS_SYSTEM, user


# ── Builder 5: explainer ──────────────────────────────────────
_EXPLAINER_SYSTEM = (
    "You are an educator and journalist at WNX24. "
    "Make the complex simple without dumbing it down. Assume "
    "the audience knows nothing about this topic. Treat them "
    "as intelligent people who need context. Use analogies. "
    "Avoid all jargon. "
    + _MARKER_INSTRUCTION.format(
        markers="[HOOK], [OPEN], [THE_BASICS], [WHAT_HAPPENED], "
                "[WHY_IT_MATTERS], [THE_BIG_PICTURE], [SIGN_OFF]"
    )
)

def build_explainer_prompt(stories: List[VerifiedStory], show: ShowConfig) -> Tuple[str, str]:
    story       = stories[0]
    word_target = _word_target(show)
    anchor_open = _anchor_open(show)

    user = f"""Write an EXPLAINER script for Aria — an educational segment
that explains ONE story or concept simply.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum

STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}
{_UNIVERSAL_RULES}

EXPLAINER RULES:
— ONE concept only — never more than one.
— Assume the audience knows nothing about this topic.
— Use analogies: "Think of it like...", "Imagine you are...",
  "It works the same way as...".
— Explain every piece of jargon immediately.
— Build from simple to complex — never the reverse.
— Maximum 15 words per sentence.

FORMAT:

[HOOK]
A question that makes the audience think. Example: "Why does
a decision in Washington affect your grocery bill in Karachi?"

[OPEN]
Use exactly: "{anchor_open}"

[THE_BASICS]
REQUIREMENT: Minimum 50 words. What is this? Who is involved?
Why does it exist? Assume zero knowledge. Use analogies.

[WHAT_HAPPENED]
REQUIREMENT: Minimum 50 words. According to
{story.raw.source_name}, the specific recent event — simple
and factual, one development at a time.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 50 words. The real-world connection for
ordinary people — jobs, prices, safety, health. Genuine
impact only — do not force a regional angle.

[THE_BIG_PICTURE]
REQUIREMENT: Minimum 40 words. What does this tell us about
where the world is heading? "Analysts suggest...", "Experts
note...", "Observers say...". What should the audience watch
for next?

[SIGN_OFF]
"{config.anchor.sign_off}"
{_sign_off_line(show)}
"""
    return _EXPLAINER_SYSTEM, user


# ── Builder 6: regional specialist correspondents ────────────
_REGIONAL_SYSTEM_PROMPTS = {
    "asia":          "You are the WNX24 Asia correspondent. Deep knowledge of the Far East, ASEAN and the Pacific.",
    "mideast":       "You are the WNX24 Middle East correspondent. Deep knowledge of the Gulf, the Levant and North Africa.",
    "science":       "You are the WNX24 science correspondent. Make research accessible. Evidence only.",
    "health":        "You are the WNX24 health correspondent. Never alarmist. Always evidence-based. Viewers should consult doctors for advice.",
    "conflict":      "You are the WNX24 conflict correspondent. Sober, factual. Never glorify violence. Use passive language for casualties always.",
    "economy":       "You are the WNX24 economics correspondent. Translate financial jargon for ordinary people.",
    "world_affairs": "You are the WNX24 diplomatic correspondent. Cover power, policy and their human cost.",
}

_REGIONAL_EXTRA_RULES = {
    "health": (
        "— Never alarmist — always evidence-based. End "
        "WHY_IT_MATTERS by noting viewers with health concerns "
        "should consult a qualified medical professional.\n"
    ),
    "conflict": (
        '— Use passive language for all casualties: "lives '
        'were lost in the strike", never "they killed 47 '
        'people".\n'
    ),
}

def build_regional_prompt(stories: List[VerifiedStory], show: ShowConfig) -> Tuple[str, str]:
    word_target  = _word_target(show)
    anchor_open  = _anchor_open(show)
    stories_text = _format_stories_block(stories)

    base_system = _REGIONAL_SYSTEM_PROMPTS.get(
        show.id, "You are a WNX24 specialist correspondent."
    )
    system = base_system + " " + _MARKER_INSTRUCTION.format(
        markers="[HOOK], [OPEN], [WHAT_HAPPENED], [WHY_IT_MATTERS], [SIGN_OFF]"
    )

    extra_rules = _REGIONAL_EXTRA_RULES.get(show.id, "")

    user = f"""Write a {show.name} script covering {len(stories)} stories.

ANCHOR:    {config.anchor.name}
OPEN WITH: "{anchor_open}"
SIGN OFF:  "{config.anchor.sign_off}"
TARGET:    {word_target} words minimum ({show.video_duration} seconds)

{stories_text}
{_UNIVERSAL_RULES}

REGIONAL DESK RULES:
— Facts relevant to this region or sector only — save global
  analysis for Deep Dive.
{extra_rules}— ONE WHY_IT_MATTERS — genuine regional or sector
  significance, never forced.
— Maximum 20 words per sentence.

FORMAT:

[HOOK]
Regional or sector angle immediately. Maximum 15 words.

[OPEN]
Use exactly: "{anchor_open}"

[WHAT_HAPPENED]
Facts first, source attributed, active voice — cover each
story in turn.

[WHY_IT_MATTERS]
REQUIREMENT: Minimum 60 words. Genuine regional or sector
significance — never forced global analysis.

[SIGN_OFF]
"{config.anchor.sign_off}"
{_sign_off_line(show)}
"""
    return system, user


# ═════════════════════════════════════════════════════════════
# DISPATCH TABLE
# ═════════════════════════════════════════════════════════════

PROMPT_BUILDERS = {
    "breaking":      (build_breaking_prompt,  0.3),
    "morning":       (build_bulletin_prompt,  0.7),
    "midday":        (build_bulletin_prompt,  0.7),
    "evening":       (build_bulletin_prompt,  0.7),
    "summary":       (build_bulletin_prompt,  0.7),
    "pakistan":      (build_pakistan_prompt,  0.7),
    "deep_dive":     (build_analysis_prompt,  0.6),
    "primetime":     (build_analysis_prompt,  0.6),
    "week_review":   (build_analysis_prompt,  0.6),
    "explainer":     (build_explainer_prompt, 0.6),
    "asia":          (build_regional_prompt,  0.7),
    "mideast":       (build_regional_prompt,  0.7),
    "science":       (build_regional_prompt,  0.7),
    "health":        (build_regional_prompt,  0.7),
    "conflict":      (build_regional_prompt,  0.7),
    "economy":       (build_regional_prompt,  0.7),
    "world_affairs": (build_regional_prompt,  0.7),
}


# ── Main agent function ────────────────────────────────────
def run_script_writer(
    stories: List[VerifiedStory],
    show: ShowConfig
) -> VideoScript:

    if not config.api.groq_api_key:
        raise ValueError("GROQ_API_KEY missing from .env")

    if not stories:
        raise ValueError("run_script_writer called with no stories")

    builder, temperature = PROMPT_BUILDERS.get(
        show.id, (build_bulletin_prompt, 0.7)
    )
    selected = stories[:max(1, show.story_count)]

    logger.info(
        f"Writing {show.name} script "
        f"({len(selected)} story{'s' if len(selected) != 1 else ''})"
    )

    system_content, user_content = builder(selected, show)
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]

    word_target = _word_target(show)
    max_tokens  = min(4000, max(700, word_target * 2))

    raw_script = _call_groq_with_retry(
        messages, max_tokens=max_tokens, temperature=temperature,
    )
    script = parse_script(raw_script, selected[0].raw.id, show)

    # ── Word count enforcement (maximum 2 retries) ──────────
    for _ in range(2):
        if show.id == "breaking":
            if script.word_count > 100:
                fix = "Shorten to maximum 80 words. Remove all analysis."
            elif script.word_count < 40:
                fix = "Add the missing facts: who, where, when, what next."
            else:
                break
        else:
            if script.word_count < word_target - 20:
                fix = "Expand each section by 20-30 words. More detail needed."
            else:
                break

        logger.warning(
            f"{show.name} script is {script.word_count} words — retrying"
        )
        messages.append({"role": "assistant", "content": raw_script})
        messages.append({"role": "user", "content": fix})
        try:
            raw_script = _call_groq_with_retry(
                messages, max_tokens=max_tokens, temperature=temperature,
            )
            script = parse_script(raw_script, selected[0].raw.id, show)
        except Exception as e:
            logger.warning(
                f"{show.name} retry failed ({e}) — using prior script"
            )
            break

    min_floor = 30 if show.id == "breaking" else 50
    if script.word_count < min_floor:
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
