"""agents/editorial_standards.py — WNX24's journalism charter.

Deterministic constants (no LLM reasoning) — per
https://agentfactory.panaversity.org/'s Gate 1a, this is a fixed
reference table, not agent work. Single source of truth for the
newsroom principles every script must follow (script_writer_agent.py)
and every review checks against (compliance_agent.py) — kept in one
place so the writer's instructions and the reviewer's checklist can
never drift apart, the same reason seo_agent.py reuses
thumbnail_agent's headline instead of generating its own.
"""

# ── The nine journalism principles ─────────────────────────
JOURNALISM_PRINCIPLES: dict[str, str] = {
    "Accuracy First":     "Never publish something unless reasonably verified. Speed is secondary.",
    "Truth Over Opinion": "Report facts, not personal beliefs.",
    "Neutral Language":   "Avoid emotional or biased wording.",
    "Multiple Sources":   "Confirm with two or more independent sources whenever possible.",
    "Attribution":        "Clearly state where information came from.",
    "Transparency":       "If something isn't confirmed, say so.",
    "Accountability":     "Correct mistakes publicly.",
    "Independence":       "Editorial decisions remain separate from commercial interests.",
    "Public Interest":    "Ask whether publishing benefits the public.",
}

# ── PEMRA (Pakistan Electronic Media Regulatory Authority) rules
# for Pakistan-related content — unchanged from the block that used
# to live inline in script_writer_agent.py's _UNIVERSAL_RULES.
PEMRA_RULES: list[str] = [
    'Military and judiciary: facts plus attribution only — '
    '"According to ISPR, troops were deployed" — never imply criticism.',
    "Religion: factual reporting only — report what officials or "
    "experts say, never an opinion on any religion.",
    'Pakistan court cases: objective facts only — "The court heard '
    'arguments today" — never comment on the merits.',
    'Banned organisations: report events, not their stated reasons — '
    '"An attack occurred in Peshawar".',
    "Nothing that could incite violence, sectarian hatred or "
    "discrimination against any group in Pakistan.",
]


def render_principles_block() -> str:
    """Render JOURNALISM_PRINCIPLES as a prompt-ready bullet list."""
    return "\n".join(
        f"— {name}: {meaning}"
        for name, meaning in JOURNALISM_PRINCIPLES.items()
    )


def render_pemra_block() -> str:
    """Render PEMRA_RULES as a prompt-ready, indented bullet list for
    Pakistan-related content."""
    return "\n".join(f"  — {rule}" for rule in PEMRA_RULES)
