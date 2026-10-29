"""agents/thumbnail_agent.py — Thumbnail Agent.

Deterministic templating (no LLM reasoning) — per
https://agentfactory.panaversity.org/'s Gate 1a, compositing a
headline/background/badge onto a fixed 1280x720 canvas with PIL is
"one exact step that is the same every time," not agent work. The
headline text itself is delegated to agents/headline_agent.py (a
genuine agent), with the raw story title as a fallback if that call
fails.
"""
import os
import textwrap
from datetime import datetime

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from config.settings import config
from core.models import Thumbnail, VerifiedStory, VideoScript

# ── Constants ──────────────────────────────────────────────
W, H        = 1280, 720
FONT_DIR    = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
OUTPUT_DIR  = os.path.join(config.output_dir, "thumbnails")

# ── Colours — WNX24 brand ───────────────────────────────
COLOURS = {
    "bg_dark":      "#0d1117",
    "bg_panel":     "#161b22",
    "accent_red":   "#e63946",
    "accent_blue":  "#1d8cf8",
    "accent_gold":  "#ffd700",
    "text_white":   "#ffffff",
    "text_grey":    "#8b949e",
    "ticker_bg":    "#e63946",
    "ticker_text":  "#ffffff",
}

# ── Show accent colours ────────────────────────────────────
SHOW_ACCENTS = {
    "breaking":     "#e63946",
    "morning":      "#1d8cf8",
    "evening":      "#7b2d8b",
    "weekly":       "#ffd700",
    "cybersec":     "#00ff88",
    "cybersecurity":"#00ff88",
    "asia":         "#1d8cf8",
    "midday":       "#1d8cf8",
    "deep_dive":    "#ffd700",
    "mideast":      "#e63946",
    "explainer":    "#17c3b2",
    "pakistan":     "#2ecc71",
    "world_affairs":"#4a90d9",
    "economy":      "#f4b400",
    "science":      "#9b59b6",
    "health":       "#48dbfb",
    "conflict":     "#c0392b",
    "week_review":  "#ffd700",
    "primetime":    "#7b2d8b",
    "summary":      "#1d8cf8",
    "default":      "#1d8cf8",
}


def hex_rgb(colour: str) -> tuple:
    h = colour.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    try:
        name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except Exception:
        try:
            font_name = "arialbd.ttf" if bold else "arial.ttf"
            return ImageFont.truetype(f"C:/Windows/Fonts/{font_name}", size)
        except Exception:
            return ImageFont.load_default()


def draw_rounded_rect(
    draw:   ImageDraw.ImageDraw,
    coords: tuple,
    radius: int = 8,
    fill:   tuple = (0, 0, 0),
) -> None:
    x0, y0, x1, y1 = coords
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def make_headline(title: str, max_words: int = 5) -> str:
    words = title.split()
    if len(words) <= max_words:
        return title.upper()
    return " ".join(words[:max_words]).upper() + "..."


# ── Core thumbnail builder ─────────────────────────────────
def create_thumbnail(
    story:   VerifiedStory,
    script:  VideoScript,
    show_id: str = "breaking",
) -> Thumbnail:

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    img  = Image.new("RGB", (W, H), hex_rgb(COLOURS["bg_dark"]))
    draw = ImageDraw.Draw(img)

    accent = SHOW_ACCENTS.get(show_id, "#1d8cf8")

    # ── Left accent bar ────────────────────────────────────
    draw.rectangle([0, 0, 8, H], fill=hex_rgb(accent))

    # ── Top bar ────────────────────────────────────────────
    draw.rectangle([0, 0, W, 80], fill=hex_rgb(COLOURS["bg_panel"]))
    draw.rectangle([0, 76, W, 80], fill=hex_rgb(accent))

    # Channel name
    draw.text(
        (24, 38),
        "WNX24",
        fill   = hex_rgb(COLOURS["text_white"]),
        font   = load_font(36, bold=True),
        anchor = "lm",
    )

    # Date — top right
    draw.text(
        (W - 24, 38),
        datetime.now().strftime("%d %b %Y"),
        fill   = hex_rgb(COLOURS["text_grey"]),
        font   = load_font(22),
        anchor = "rm",
    )

    # ── Show badge ─────────────────────────────────────────
    badge_text = {
        "breaking":     "● BREAKING",
        "morning":      "● MORNING",
        "asia":         "● ASIA REPORT",
        "midday":       "● MIDDAY",
        "deep_dive":    "● DEEP DIVE",
        "mideast":      "● MIDEAST",
        "evening":      "● EVENING",
        "cybersecurity":"● CYBER WATCH",
        "primetime":    "● PRIME TIME",
        "summary":      "● DAILY SUMMARY",
        "explainer":    "● EXPLAINER",
        "pakistan":     "● PAKISTAN",
        "world_affairs":"● WORLD AFFAIRS",
        "economy":      "● MARKETS",
        "science":      "● SCIENCE",
        "health":       "● HEALTH",
        "conflict":     "● CONFLICT",
        "week_review":  "● WEEK REVIEW",
    }.get(show_id, "● LIVE")

    font_badge = load_font(24, bold=True)
    badge_w    = int(draw.textlength(badge_text, font=font_badge)) + 32

    draw_rounded_rect(
        draw,
        (W - badge_w - 24, 88, W - 24, 128),
        radius = 6,
        fill   = hex_rgb(accent),
    )
    draw.text(
        ((W - badge_w - 24 + W - 24) // 2, 108),
        badge_text,
        fill   = hex_rgb(COLOURS["text_white"]),
        font   = font_badge,
        anchor = "mm",
    )

    # ── Accent line under headline area ────────────────────
    draw.rectangle([24, 148, 180, 154], fill=hex_rgb(accent))

    # ── Main headline ──────────────────────────────────────
    # headline_agent gives a punchier, LLM-written headline than the raw
    # scraped title; fall back to the title itself if that call fails so
    # a thumbnail can never fail to render because of it.
    try:
        from agents.headline_agent import run_headline_agent
        raw_headline = run_headline_agent(story)
    except Exception as e:
        logger.warning(f"Headline Agent failed, falling back to story title: {e}")
        raw_headline = story.raw.title
    headline = make_headline(raw_headline, max_words=8)

    if len(headline) <= 30:
        fnt = load_font(68, bold=True)
    elif len(headline) <= 50:
        fnt = load_font(54, bold=True)
    else:
        fnt = load_font(44, bold=True)

    wrapped = textwrap.fill(headline, width=20)
    y_start = 168

    for line in wrapped.split("\n"):
        draw.text(
            (24, y_start),
            line,
            fill = hex_rgb(COLOURS["text_white"]),
            font = fnt,
        )
        y_start += fnt.size + 10

    # ── Hook line ──────────────────────────────────────────
    if script and script.hook:
        hook_text    = script.hook[:80]
        font_hook    = load_font(30, bold=True)
        wrapped_hook = textwrap.fill(hook_text, width=52)

        hook_y    = max(y_start + 40, H - 220)
        hook_bbox = draw.textbbox((24, hook_y), wrapped_hook, font=font_hook)
        draw_rounded_rect(
            draw,
            (16, hook_bbox[1] - 8, hook_bbox[2] + 16, hook_bbox[3] + 8),
            radius = 4,
            fill   = hex_rgb(COLOURS["bg_panel"]),
        )
        draw.text(
            (24, hook_y),
            wrapped_hook,
            fill = hex_rgb(COLOURS["accent_gold"]),
            font = font_hook,
        )

    # ── Source — single line ───────────────────────────────
    draw.text(
        (24, H - 90),
        f"Source: {story.raw.source_name}",
        fill = hex_rgb(COLOURS["text_grey"]),
        font = load_font(20),
    )

    # ── Bottom ticker ──────────────────────────────────────
    draw.rectangle([0, H - 60, W, H], fill=hex_rgb(COLOURS["ticker_bg"]))

    font_ticker = load_font(26, bold=True)
    timestamp   = datetime.now().strftime("%d %b %Y  |  %H:%M PKT")

    draw.text(
        (20, H - 30),
        "● LIVE",
        fill   = hex_rgb(COLOURS["text_white"]),
        font   = font_ticker,
        anchor = "lm",
    )
    draw.text(
        (W // 2, H - 30),
        f"WNX24  |  {timestamp}",
        fill   = hex_rgb(COLOURS["text_white"]),
        font   = load_font(22),
        anchor = "mm",
    )
    draw.text(
        (W - 20, H - 30),
        story.raw.source_name.upper()[:25],
        fill   = hex_rgb(COLOURS["text_white"]),
        font   = load_font(20),
        anchor = "rm",
    )

    # ── WNX24 logo watermark ──────────────────────────────
    logo_path = "assets/WNX24_thumb_logo.png"
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((80, 80), Image.LANCZOS)
            # Position: top right corner
            img.paste(logo, (img.width - 90, 10), logo)
        except Exception as e:
            logger.warning(f"Could not add logo watermark: {e}")

    # ── Save ───────────────────────────────────────────────
    filename   = f"thumb_{story.raw.id}_{show_id}.jpg"
    image_path = os.path.join(OUTPUT_DIR, filename)
    img.save(image_path, "JPEG", quality=95)
    logger.info(f"Thumbnail saved — {image_path}")

    return Thumbnail(
        story_id   = story.raw.id,
        headline   = headline,
        image_path = image_path,
    )


# ── Main agent function ────────────────────────────────────
def run_thumbnail_agent(
    story:   VerifiedStory,
    script:  VideoScript,
    show_id: str = "breaking",
) -> Thumbnail:

    logger.info("Starting Thumbnail Agent — WNX24")
    thumbnail = create_thumbnail(story, script, show_id)
    logger.info(f"Thumbnail Agent complete — {thumbnail.image_path}")
    return thumbnail
