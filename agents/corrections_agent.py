"""agents/corrections_agent.py — Corrections Agent.

A genuine agent per https://agentfactory.panaversity.org/'s Gate 1a:
drafting a clear, honest, non-defensive public correction from a
human-supplied description of what was wrong needs LLM judgment over
free text — this is where WNX24's journalism charter's "Accountability"
principle (agents/editorial_standards.py) actually gets enforced,
rather than just stated in a prompt.

Detecting that a mistake happened isn't automatable from this
pipeline's own data — nothing here disputes a story after the fact —
so a human flags it via dashboard.app's POST /correct/{story_id}, the
same way a real newsroom relies on editors or viewers to catch errors,
not an algorithm. This agent only handles what happens *after* that
flag: draft the correction, publish it, record it.

Gate 3 (what "finished" looks like):
  Works from  — a story_id with an existing broadcast, plus a human's
                note on what was wrong
  Want at end — a public correction posted to the broadcast's YouTube
                video (description + top comment) and a `corrections`
                DB row for audit, even if the video was a mock upload
  Done when   — dashboard.app's /correct/{story_id} returns the
                published correction text and comment id (if any)
"""
import re
from datetime import UTC, datetime

from loguru import logger

from agents.editorial_standards import render_principles_block
from agents.script_writer_agent import _call_groq_with_retry
from agents.uploader_agent import get_youtube_client
from core.database import get_broadcast_by_story_id, save_correction

_SYSTEM_PROMPT = (
    "You are the corrections editor for WNX24, a 24/7 AI news channel. "
    "A human editor has flagged that a previously published broadcast "
    "contained an error. WNX24's journalism charter requires this:\n"
    f"{render_principles_block()}\n\n"
    "Write a short, honest, non-defensive public correction — state "
    "plainly what was wrong and what the correct information is. Do "
    "not minimise, hedge, or over-apologise. Two or three sentences. "
    "Output only the correction text, nothing else."
)

_VIDEO_ID_RE = re.compile(r"[?&]v=([\w-]{6,})")


def draft_correction(original_title: str, note: str) -> str:
    """Draft the public correction text via LLM."""
    user_prompt = f"""ORIGINAL BROADCAST: {original_title}

WHAT AN EDITOR FLAGGED AS WRONG / NEEDING CORRECTION:
{note}

Write the public correction now."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return _call_groq_with_retry(
        messages, max_tokens=200, temperature=0.3, min_words=5
    ).strip()


def extract_video_id(youtube_url: str) -> str | None:
    m = _VIDEO_ID_RE.search(youtube_url or "")
    return m.group(1) if m else None


def publish_correction(video_id: str, correction_text: str) -> str | None:
    """Publish `correction_text` to the video: prepend it to the
    description and post it as a top-level comment. Returns the
    comment id if posted, else None — description update and comment
    post are each best-effort (e.g. comments can be disabled on a
    video independent of the description succeeding)."""
    youtube = get_youtube_client()
    if youtube is None:
        logger.warning("No usable YouTube token — correction not published")
        return None

    banner = (
        f"⚠️ CORRECTION ({datetime.now(UTC).strftime('%d %b %Y')}): "
        f"{correction_text}\n\n"
    )

    try:
        video = youtube.videos().list(part="snippet", id=video_id).execute()
        items = video.get("items", [])
        if items:
            snippet = items[0]["snippet"]
            snippet["description"] = banner + snippet.get("description", "")
            youtube.videos().update(
                part="snippet", body={"id": video_id, "snippet": snippet}
            ).execute()
            logger.info(f"Correction prepended to description for {video_id}")
        else:
            logger.warning(f"Video {video_id} not found on YouTube")
    except Exception as e:
        logger.warning(f"Could not update video description for {video_id}: {e}")

    comment_id = None
    try:
        response = youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": banner.strip()}
                    },
                }
            },
        ).execute()
        comment_id = response["id"]
        logger.info(f"Correction comment posted on {video_id}: {comment_id}")
    except Exception as e:
        logger.warning(f"Could not post correction comment on {video_id}: {e}")

    return comment_id


def run_corrections_agent(story_id: str, note: str) -> dict:
    """Main entry point — dashboard.app's POST /correct/{story_id}
    calls this. Drafts a correction, publishes it if the story's
    broadcast has a real (non-mock) YouTube video, and always records
    it in the `corrections` table regardless of whether publishing
    was possible."""
    logger.info(f"Starting Corrections Agent — story {story_id}")

    broadcast = get_broadcast_by_story_id(story_id)
    if broadcast is None:
        raise ValueError(f"No broadcast found for story_id {story_id}")

    correction_text = draft_correction(broadcast["story_id"], note)

    youtube_url = broadcast["youtube_url"] or ""
    video_id    = extract_video_id(youtube_url)
    comment_id  = None
    published   = False

    if video_id and "MOCK" not in youtube_url:
        comment_id = publish_correction(video_id, correction_text)
        published  = True
    else:
        logger.warning(
            f"Story {story_id} has no real YouTube video "
            f"(mock or missing) — correction recorded only"
        )

    save_correction(
        story_id        = story_id,
        show_id         = broadcast["show_id"],
        youtube_url     = youtube_url,
        original_note   = note,
        correction_text = correction_text,
        published       = published,
    )

    logger.info(f"Corrections Agent complete — published={published}")
    return {
        "story_id":           story_id,
        "correction_text":    correction_text,
        "youtube_url":        youtube_url,
        "youtube_comment_id": comment_id,
        "published":          published,
    }
