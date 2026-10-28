"""agents/planning_agent.py — Planning Agent.

A genuine agent per https://agentfactory.panaversity.org/'s Gate 1a:
picking which of several similarly-scored, already-verified stories
best fits a *specific* show's focus and audience — and avoiding two
shows running the same lead the same day — needs editorial judgment
over an unpredictable candidate pool, not a fixed sort. The candidate
pool itself is still built deterministically (confidence/score order
from core/database.get_fresh_stories) — this agent only re-picks/
reorders within it.

Gate 2 (once, or every week?): runs before every non-breaking
broadcast — daily, same shape, worth it for lineup quality.

Gate 3 (what "finished" looks like):
  Works from  — candidate VerifiedStory list (already score-sorted) + ShowConfig
  Want at end — the same stories, selected/reordered to show.story_count
  Done when   — returns exactly show.story_count VerifiedStory objects
                drawn from the candidates; falls back to the plain
                top-N-by-score slice orchestrator.py used before this
                agent existed if the LLM call or parse fails.
"""
import re

from loguru import logger

from agents.script_writer_agent import _call_groq_with_retry
from config.settings import ShowConfig
from core.models import VerifiedStory

_SYSTEM_PROMPT = (
    "You are the assignment editor for WNX24, a 24/7 AI news channel. "
    "Given a numbered list of already-verified candidate stories and "
    "one show's focus, choose the best stories for THAT show and order "
    "them lead-story first. Prefer: genuine fit with the show's focus; "
    "higher real-world impact over marginal updates; variety over near-"
    "duplicate stories covering the same underlying event. "
    "When the show's focus is 'global' (not a specific region), also "
    "actively spread the picks across continents — the Americas, "
    "Europe, Africa, the Middle East, and Asia — rather than filling "
    "the lineup with stories from one region just because that region "
    "produced the most or highest-scoring candidates today. A lineup "
    "that is 8 of 10 stories from one continent is a bad lineup even "
    "if each individual story scored well; genuinely prefer a lower-"
    "scoring story from an underrepresented continent over yet another "
    "story from a continent already well covered by other picks. "
    "Respond with ONLY a comma-separated list of the chosen candidate "
    "numbers, most important first — nothing else. Example: 3,1,7"
)

# Bound the prompt (and Groq token cost) regardless of how many
# verified stories are actually fresh in the DB. get_fresh_stories()
# already returns its own pool sorted confidence DESC, score DESC — a
# narrow window here (this used to be 15) meant a heavy-news-day story
# that dominates cross-source confirmation (e.g. one huge breaking event
# covered by 20+ outlets) could fill this whole window before the LLM
# ever saw a lower-scoring story from an underrepresented region, making
# the "spread across continents" instruction above moot in practice.
MAX_CANDIDATES = 30

# Sources for regions that structurally score lower in
# agents/verification_agent.py's confidence formula — not because the
# reporting is worse, but because there are fewer overlapping wire
# services to cross-confirm a story the way BBC/Reuters/AP/CNN/Washington
# Post routinely do for US/UK/EU stories. Reserving a couple of pool
# slots for the best of these (even if their raw confidence rank would
# otherwise put them past MAX_CANDIDATES on any day with a big Western
# story dominating the feed) is what actually makes the "spread across
# continents" instruction reachable rather than aspirational — a slice
# built by pure confidence rank alone reliably excludes them on such days.
_UNDERREPRESENTED_SOURCES = {"BBC Africa", "Africanews", "AllAfrica", "News24"}
_RESERVED_SLOTS = 3


def _build_pool(candidates: list[VerifiedStory], max_candidates: int) -> list[VerifiedStory]:
    top = candidates[:max_candidates]
    if any(s.raw.source_name in _UNDERREPRESENTED_SOURCES for s in top):
        return top  # already represented — no need to force anything in

    reserve_from = [
        s for s in candidates[max_candidates:]
        if s.raw.source_name in _UNDERREPRESENTED_SOURCES
    ]
    if not reserve_from:
        return top  # nothing further down the pool to reserve a slot for

    reserved = reserve_from[:_RESERVED_SLOTS]
    pool = top[: max_candidates - len(reserved)] + reserved
    pool.sort(key=lambda s: s.confidence_score, reverse=True)
    return pool


def _format_candidates(candidates: list[VerifiedStory]) -> str:
    lines = []
    for i, s in enumerate(candidates, 1):
        lines.append(
            f"[{i}] {s.raw.title} — {s.raw.source_name} "
            f"(confidence {s.confidence_score:.2f})"
        )
    return "\n".join(lines)


def plan_lineup(
    candidates: list[VerifiedStory], show: ShowConfig
) -> list[VerifiedStory]:
    """Select and order `show.story_count` stories from `candidates` for
    `show`. Always returns a non-empty list if `candidates` is
    non-empty — falls back to the deterministic top-N slice on any
    LLM failure or unparseable response."""
    n = max(1, show.story_count)
    fallback = _build_pool(candidates, n)

    if len(candidates) <= n:
        return candidates

    pool = _build_pool(candidates, MAX_CANDIDATES)
    user_prompt = f"""SHOW: {show.name}
FOCUS: {show.regional_focus}
PICK: {n} stories

CANDIDATES:
{_format_candidates(pool)}
"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        # gpt-oss-120b spends a good chunk of its completion-token budget on
        # hidden reasoning even at reasoning_effort="low" — 60 tokens was
        # consistently consumed entirely by that reasoning, leaving zero
        # tokens for the actual pick list and silently falling back to
        # plain score order (defeating the "variety over near-duplicate
        # stories"/continental-spread instructions above). Reasoning-token
        # usage varies call to call — 250 still wasn't always enough once
        # the longer continental-diversity instructions were added, so
        # this has real headroom now rather than being tuned to the
        # minimum that happened to pass a couple of manual tests.
        raw = _call_groq_with_retry(messages, max_tokens=500, temperature=0.3, min_words=1)
    except Exception as e:
        logger.warning(f"Planning Agent LLM call failed, using score order: {e}")
        return fallback

    indices = []
    for tok in re.findall(r"\d+", raw):
        i = int(tok)
        if 1 <= i <= len(pool) and i not in indices:
            indices.append(i)

    if len(indices) < n:
        logger.warning(
            f"Planning Agent returned {len(indices)} usable picks "
            f"(need {n}) — filling remainder from score order"
        )
        for i in range(1, len(pool) + 1):
            if i not in indices:
                indices.append(i)
            if len(indices) >= n:
                break

    selected = [pool[i - 1] for i in indices[:n]]
    return selected


def run_planning_agent(
    candidates: list[VerifiedStory], show: ShowConfig
) -> list[VerifiedStory]:
    """Main entry point."""
    logger.info(
        f"Starting Planning Agent — {show.name} "
        f"({len(candidates)} candidates, need {show.story_count})"
    )
    selected = plan_lineup(candidates, show)
    logger.info(
        f"Planning Agent complete — lead: {selected[0].raw.title[:55]}"
    )
    return selected
