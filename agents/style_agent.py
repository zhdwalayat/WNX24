"""agents/style_agent.py — Editorial Style Agent.

Deterministic linting (no LLM reasoning) — per
https://agentfactory.panaversity.org/'s Gate 1a: checking a finished
script against WNX24's fixed house style guide (banned filler words,
British vs. American spelling, sentence length) is "one exact step
that is the same every time," a style-guide checklist, not a judgment
call — even though script_writer_agent.py's writer LLM is separately
instructed to follow the same rules while drafting (see
_UNIVERSAL_RULES). This plays the same "second independent read" role
agents/compliance_agent.py plays for legal risk, but for house style:
informational rather than a hard gate — style slips get logged for a
human style editor to spot-check, they don't block an unattended
broadcast the way legal risk does.

Gate 3 (what "finished" looks like):
  Works from  — a rendered VideoScript
  Want at end — a StyleReport: banned words, American-spelling slips,
                and over-length sentences found
  Done when   — orchestrator.py logs the report after script writing
                (does not gate run_broadcast() on it)
"""
import re

from loguru import logger

from core.models import StyleReport, VideoScript

# Mirrors script_writer_agent.py's _UNIVERSAL_RULES banned-word list —
# kept as a separate constant here since that list lives inline in a
# prompt string, not as an importable constant.
BANNED_WORDS = [
    "claims", "admits", "slammed", "bombshell", "shocking",
    "dramatic", "devastating",
]

# American spelling variants that violate "British spelling throughout" —
# not exhaustive, just the words this newsroom's copy tends to slip on.
AMERICAN_SPELLINGS = {
    "color": "colour", "favorite": "favourite", "organize": "organise",
    "organization": "organisation", "realize": "realise", "analyze": "analyse",
    "center": "centre", "defense": "defence", "license": "licence",
    "traveled": "travelled", "canceled": "cancelled", "labor": "labour",
    "neighbor": "neighbour", "honor": "honour", "criticize": "criticise",
    "recognize": "recognise", "apologize": "apologise", "program": "programme",
}

MAX_SENTENCE_WORDS = 30


def _sections(script: VideoScript) -> list[str]:
    return [p for p in (
        script.hook, script.what_happened, script.why_it_matters,
        script.expert_insight, script.forward_look, script.cta,
    ) if p]


def check_style(script: VideoScript) -> StyleReport:
    """Lint a finished script against WNX24's house style guide."""
    full_text = " ".join(_sections(script))
    words = re.findall(r"[A-Za-z']+", full_text)
    lowered = {w.lower() for w in words}

    issues: list[str] = []

    for banned in BANNED_WORDS:
        if re.search(rf"\b{banned}\b", full_text, re.IGNORECASE):
            issues.append(f"Banned word used: '{banned}'")

    for american, british in AMERICAN_SPELLINGS.items():
        if american in lowered:
            issues.append(f"American spelling '{american}' — use '{british}'")

    sentences = re.split(r"(?<=[.!?])\s+", full_text)
    long_sentences = [s for s in sentences if len(s.split()) > MAX_SENTENCE_WORDS]
    if long_sentences:
        issues.append(
            f"{len(long_sentences)} sentence(s) over {MAX_SENTENCE_WORDS} words"
        )

    return StyleReport(
        story_id = script.story_id,
        passed   = not issues,
        issues   = issues,
    )


def run_style_agent(script: VideoScript) -> StyleReport:
    """Main entry point."""
    logger.info(f"Starting Style Agent — story {script.story_id}")
    report = check_style(script)
    if report.passed:
        logger.info("Style Agent: no issues found")
    else:
        logger.warning(
            f"Style Agent found {len(report.issues)} issue(s): "
            f"{'; '.join(report.issues)}"
        )
    return report
