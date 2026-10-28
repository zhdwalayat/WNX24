"""agents/web_search_agent.py — general web search tool.

Deterministic tool call (no LLM reasoning) — this is the "hands", not
the "brain", per the agent-vs-code split described at
https://agentfactory.panaversity.org/: research_agent.py is the
reasoning layer that decides *what* to search for and *how* to use the
results; this module only knows how to run one search and return
structured hits.

Uses the free `ddgs` (DuckDuckGo) search library — no paid API key or
account required, unlike Google Custom Search / Bing / SerpAPI.
"""
from loguru import logger


def search(query: str, max_results: int = 5) -> list[dict]:
    """Run a web search and return up to `max_results` hits, each a
    dict with `title`, `url`, `snippet`. Returns an empty list (logged,
    non-fatal) on any failure — callers should treat search as
    best-effort, not a hard dependency."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        logger.warning(f"Web search failed for [{query[:50]}]: {e}")
        return []

    results = []
    for item in raw:
        title = item.get("title", "")
        url   = item.get("href", "")
        if not title or not url:
            continue
        results.append({
            "title":   title,
            "url":     url,
            "snippet": item.get("body", ""),
        })
    return results


def run_web_search_agent(query: str, max_results: int = 5) -> list[dict]:
    """Main entry point — thin wrapper kept for naming consistency with
    the other agents in this package."""
    logger.info(f"Web Search Agent — query: {query[:60]}")
    results = search(query, max_results=max_results)
    logger.info(f"Web Search Agent complete — {len(results)} results")
    return results
