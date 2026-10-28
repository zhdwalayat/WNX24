"""agents/research_agent.py — Research Agent.

This one *is* a genuine agent per the agent-vs-code split at
https://agentfactory.panaversity.org/: given a story, it decides what
to search for, reasons over a variable, unpredictable set of web
results (the model/brain), and synthesises them into a research brief.
Contrast with web_search_agent.py, which is just the tool/hand it uses
to fetch those results — that part is a fixed API call, so it isn't an
agent itself.
"""
from loguru import logger

from agents.script_writer_agent import _call_groq_with_retry
from agents.web_search_agent import run_web_search_agent
from core.models import VerifiedStory

_SYSTEM_PROMPT = (
    "You are a research assistant for the WNX24 newsroom. Given a story "
    "and a handful of web search results, produce a short, factual "
    "research brief a scriptwriter can use for background context. "
    "Never invent facts not present in the material given to you. "
    "Attribute claims to their source. If the search results don't "
    "confirm or add anything, say so plainly rather than padding."
)


def _format_search_results(results: list[dict]) -> str:
    if not results:
        return "(no web search results found)"
    blocks = []
    for i, r in enumerate(results, 1):
        blocks.append(
            f"[{i}] {r['title']}\n{r['snippet'][:300]}\nSource: {r['url']}"
        )
    return "\n\n".join(blocks)


def build_research_brief(story: VerifiedStory, search_results: list[dict]) -> str:
    """Reason over `search_results` in the context of `story` and return
    a short research brief (background, related developments, open
    questions). This is the step that needs an LLM — synthesising an
    unpredictable set of search snippets isn't a fixed procedure."""
    user_prompt = f"""STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}

WEB SEARCH RESULTS:
{_format_search_results(search_results)}

Write a research brief with three short sections:
BACKGROUND — one or two sentences of context the summary doesn't cover.
RELATED DEVELOPMENTS — anything the search results add that's genuinely new.
OPEN QUESTIONS — what remains unconfirmed or unclear.
Keep the whole brief under 150 words. If the search results add nothing
useful, say "No additional context found" instead of padding.
"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return _call_groq_with_retry(messages, max_tokens=400, temperature=0.4)


def run_research_agent(story: VerifiedStory) -> str:
    """Main entry point — searches the web for additional context on
    `story` and returns a synthesised research brief."""
    logger.info(f"Starting Research Agent — {story.raw.title[:50]}")

    query = " ".join(story.raw.keywords[:4]) or story.raw.title
    search_results = run_web_search_agent(query, max_results=5)

    try:
        brief = build_research_brief(story, search_results)
    except Exception as e:
        logger.error(f"Research Agent LLM synthesis failed: {e}")
        brief = "No additional context found."

    logger.info("Research Agent complete")
    return brief
