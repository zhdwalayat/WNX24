"""agents/seo_agent.py — SEO Agent.

Deterministic templating (no LLM reasoning) — per
https://agentfactory.panaversity.org/'s agent-vs-code guidance, this is
a fixed procedure (fill a title/description/tags template from script
fields), so it's a plain module, not an autonomous agent. Extracted out
of uploader_agent.py so it can be reused/tested independently of the
YouTube upload flow. The video title reuses package.thumbnail.headline
(generated once by headline_agent for the thumbnail) rather than making
its own LLM call, so title and thumbnail never disagree.
"""
from datetime import datetime

from config.settings import ShowConfig, config
from core.models import VideoPackage

CATEGORY_ID = "25"   # 25 = News & Politics on YouTube

# YouTube hard-rejects (400 invalidDescription) any description over 5000
# characters — a single-story script fits comfortably, but a 10-story
# bulletin's full what_happened/why_it_matters text routinely ran to
# 6000+ and made every multi-story upload silently fall back to mock.
_MAX_DESCRIPTION_CHARS = 5000


def build_chapters(script, show) -> str:
    wpm = config.content.words_per_minute

    def words_to_time(words: int) -> str:
        secs = int((words / wpm) * 60)
        mins = secs // 60
        secs = secs % 60
        return f"{mins}:{secs:02d}"

    chapters = ["0:00 Introduction"]
    offset   = 15   # words for hook + open

    if script.what_happened:
        chapters.append(f"{words_to_time(offset)} What Happened")
        offset += len(script.what_happened.split())

    if script.why_it_matters:
        chapters.append(f"{words_to_time(offset)} Why It Matters")
        offset += len(script.why_it_matters.split())

    if script.expert_insight:
        chapters.append(f"{words_to_time(offset)} Analysis")
        offset += len(script.expert_insight.split())

    chapters.append(f"{words_to_time(offset)} Sign Off")

    return "\n".join(chapters)


def build_metadata(package: VideoPackage, show: ShowConfig = None) -> dict:
    """Generate SEO-optimised YouTube metadata (title, description,
    tags, chapters) for `package`."""
    story  = package.story.raw
    script = package.script

    # Title — reuse the headline thumbnail_agent already generated (via
    # headline_agent) so the thumbnail and YouTube title stay consistent
    # and we don't spend a second LLM call on the same job. Falls back to
    # the script hook if no thumbnail headline is available (e.g. a
    # package built without one, such as in tests).
    headline_text = package.thumbnail.headline
    if not headline_text:
        headline_text = script.hook
        for prefix in ("hook:", "open:", "breaking:"):
            if headline_text.lower().startswith(prefix):
                headline_text = headline_text[len(prefix):].strip()
                break
    title = f"{headline_text[:60]} | WNX24"
    if len(title) > 100:
        title = title[:97] + "..."

    # Description — structured for YouTube SEO
    chapters = build_chapters(script, show)

    def _render(what_happened: str, why_it_matters: str) -> str:
        return f"""🤖 AI-Generated Content: This video uses AI avatar and AI script generation technology.

{script.hook}

{what_happened}

{why_it_matters}

{chapters}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 WNX24
"{config.channel.tagline}"

Covering AI, Cybersecurity, and Global Technology News
from Pakistan, the Middle East, Asia, and the World.

📌 Source: {story.source_name}
🔗 Original: {story.url}
📅 {datetime.now().strftime("%d %B %Y  |  %H:%M PKT")}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#WNX24 #AINews #TechNews #CyberSecurity
#BreakingNews #ArtificialIntelligence #GlobalNews
#PakistanNews #MiddleEast #AsiaNews #TechToday
#WorldNews #AIGeneratedContent #NewsAI
"""

    description = _render(script.what_happened, script.why_it_matters)
    if len(description) > _MAX_DESCRIPTION_CHARS:
        # A multi-story bulletin's full body text is what overflows —
        # drop why_it_matters first (a bulletin's is a combined summary,
        # not story-specific, so it's the least essential part of the
        # description), then hard-truncate what_happened if still over.
        description = _render(script.what_happened, "")
        if len(description) > _MAX_DESCRIPTION_CHARS:
            overflow = len(description) - _MAX_DESCRIPTION_CHARS + len(" [...]")
            truncated = script.what_happened[:-overflow].rstrip() + " [...]"
            description = _render(truncated, "")

    # Tags — SEO keywords
    tags = [
        "WNX24",
        "AI news", "artificial intelligence",
        "tech news today", "cybersecurity news",
        "breaking news", "global news",
        "Pakistan news", "Middle East news",
        "Asia tech news", "machine learning",
        "OpenAI", "technology news",
        story.source_name,
    ] + list(package.story.raw.keywords)[:5]

    return {
        "title":       title[:100],
        "description": description,
        "tags":        tags[:500],          # YouTube max 500 chars total
        "categoryId":  CATEGORY_ID,
        "defaultLanguage": config.channel.language,
        "privacyStatus": "public",
    }


def run_seo_agent(package: VideoPackage, show: ShowConfig = None) -> dict:
    """Main entry point."""
    return build_metadata(package, show)
