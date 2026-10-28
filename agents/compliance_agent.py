"""agents/compliance_agent.py — Compliance Agent.

A genuine agent per https://agentfactory.panaversity.org/'s Gate 1a:
judging whether a *finished* script's specific, unpredictable wording
crosses into defamation, unproven-claim-as-fact, or PEMRA-restricted
territory needs LLM judgment over free text, not a fixed lookup.
script_writer_agent.py already instructs its writer LLM to follow the
same charter while drafting (see _UNIVERSAL_RULES) — both pull their
rules from agents/editorial_standards.py so the writer's instructions
and this agent's checklist can never drift apart. This agent is the
independent second read before air, the same way a legal/compliance
editor reviews copy a reporter already tried to get right.

Gate 3 (what "finished" looks like):
  Works from  — a rendered VideoScript (+ the VerifiedStory it's based on)
  Want at end — a ComplianceReport: pass/fail, risk level, issue list
  Done when   — orchestrator.py can gate run_broadcast() on `passed`
"""
import re

from loguru import logger

from agents.editorial_standards import render_pemra_block, render_principles_block
from agents.script_writer_agent import _call_groq_with_retry
from core.models import ComplianceReport, VerifiedStory, VideoScript

_SYSTEM_PROMPT = (
    "You are the legal/compliance editor for WNX24, a 24/7 AI news "
    "channel. You review a script a writer has already drafted — you "
    "do not rewrite it. Check it against WNX24's journalism charter:\n"
    f"{render_principles_block()}\n\n"
    "Flag only genuine violations of that charter, not style "
    "preferences:\n"
    "- Accuracy First / Transparency: a claim stated as settled fact "
    "that the story material doesn't actually confirm.\n"
    "- Truth Over Opinion / Neutral Language: the script takes a side, "
    "or uses emotionally loaded rather than factual wording.\n"
    "- Attribution: a specific named person or organisation accused of "
    "wrongdoing as settled fact rather than 'alleged'/'accused of', or "
    "any claim not tied to a named source.\n"
    "- Independence / Public Interest: language that reads as "
    "promoting a product, company, or commercial interest rather than "
    "serving the audience.\n"
    "- PEMRA-restricted content for Pakistan-related material:\n"
    f"{render_pemra_block()}\n"
    "- Fabricated quotes or attributions not grounded in the story "
    "material you're given.\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "VERDICT: PASS or FAIL\n"
    "RISK: LOW or MEDIUM or HIGH\n"
    "ISSUES:\n"
    "- one issue per line, or a single line \"- None\" if there are none"
)


def _format_script(script: VideoScript) -> str:
    parts = [
        script.hook, script.what_happened, script.why_it_matters,
        script.expert_insight, script.forward_look, script.cta,
    ]
    return "\n\n".join(p for p in parts if p)


def _parse_report(raw: str, story_id: str) -> ComplianceReport:
    verdict_m = re.search(r"VERDICT:\s*(PASS|FAIL)", raw, re.IGNORECASE)
    risk_m    = re.search(r"RISK:\s*(LOW|MEDIUM|HIGH)", raw, re.IGNORECASE)

    issues = []
    in_issues = False
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("ISSUES"):
            in_issues = True
            continue
        if in_issues and line.startswith("-"):
            issue = line.lstrip("-").strip()
            if issue and issue.lower() != "none":
                issues.append(issue)

    return ComplianceReport(
        story_id   = story_id,
        passed     = verdict_m.group(1).upper() == "PASS" if verdict_m else not issues,
        risk_level = risk_m.group(1).lower() if risk_m else ("high" if issues else "low"),
        issues     = issues,
    )


def check_compliance(script: VideoScript, story: VerifiedStory) -> ComplianceReport:
    """Review `script` for defamation/legal/PEMRA risk and return a
    ComplianceReport. Never raises — an LLM failure here should not be
    silently treated as a pass, so callers get risk_level='high' and
    passed=False, forcing an explicit decision rather than a bug that
    defaults broadcasts to unreviewed."""
    user_prompt = f"""SOURCE STORY:
Title:   {story.raw.title}
Source:  {story.raw.source_name}
Summary: {story.raw.summary}

SCRIPT TO REVIEW:
{_format_script(script)}
"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw = _call_groq_with_retry(messages, max_tokens=300, temperature=0.2, min_words=3)
        return _parse_report(raw, script.story_id)
    except Exception as e:
        logger.error(f"Compliance Agent LLM call failed: {e}")
        return ComplianceReport(
            story_id=script.story_id, passed=False, risk_level="high",
            issues=[f"Compliance check could not be completed: {e}"],
        )


def run_compliance_agent(script: VideoScript, story: VerifiedStory) -> ComplianceReport:
    """Main entry point."""
    logger.info(f"Starting Compliance Agent — {story.raw.title[:50]}")
    report = check_compliance(script, story)
    if report.passed:
        logger.info(f"Compliance Agent: PASS (risk={report.risk_level})")
    else:
        logger.warning(
            f"Compliance Agent: FAIL (risk={report.risk_level}) — "
            f"{'; '.join(report.issues) or 'no details'}"
        )
    return report
