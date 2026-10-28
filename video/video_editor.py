# video/video_editor.py
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import imageio_ffmpeg
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from config.settings import config
from core.models import VideoScript

# ── Paths ──────────────────────────────────────────────────
ROOT_DIR   = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
FONT_BOLD  = str(ASSETS_DIR / "fonts" / "DejaVuSansMono-Bold.ttf")
FONT_REG   = str(ASSETS_DIR / "fonts" / "DejaVuSansMono.ttf")
NEWSROOM_BG = ASSETS_DIR / "newsroom_bg.jpg"

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ── Frame / encode settings ───────────────────────────────────
FRAME_W, FRAME_H = 1280, 720
FPS              = 24

# Content box where scene content is drawn — scaled down from
# video_composer's 1920x1080 layout (220,80)-(1330,630) to this module's
# 1280x720 frame, so the same show-menu (left), markets/clocks (right)
# and ticker (bottom) gutters of assets/newsroom_bg.jpg stay visible.
CONTENT_X0, CONTENT_Y0 = 147, 53
CONTENT_X1, CONTENT_Y1 = 887, 420
CONTENT_W = CONTENT_X1 - CONTENT_X0
CONTENT_H = CONTENT_Y1 - CONTENT_Y0

# ── Colour palette ─────────────────────────────────────────
BG_COLOUR     = (10,  12,  40)   # dark navy
ACCENT_COLOUR = (220, 50,  50)   # red ticker / badge
TEXT_COLOUR   = (240, 240, 240)  # near-white
TICKER_COLOUR = (255, 200,  40)  # gold headline

INTRO_DURATION = 3.0
OUTRO_DURATION = 4.0
INTRO_PATH = ASSETS_DIR / "intro.mp4"
OUTRO_PATH = ASSETS_DIR / "outro.mp4"
TICKER_SPEED   = 90    # px/sec scroll speed
TICKER_LOGO    = str(ASSETS_DIR / "WNX24_icon.png")

TICKER_FALLBACK = "►  WNX24 — World News Express 24  ●  World News. Always."
_TICKER_STRIP_RE = re.compile(r"[%\\\r\n\t]")

# ── Script section -> scene mapping ────────────────────────
SECTION_ORDER  = ["hook", "what_happened", "why_it_matters", "expert_insight", "cta"]
SECTION_LABELS = {
    "hook":           "BREAKING",
    "what_happened":  "WHAT HAPPENED",
    "why_it_matters": "WHY IT MATTERS",
    "expert_insight": "EXPERT INSIGHT",
    "cta":            "SIGN OFF",
}
SECTION_TEXT = {
    "hook":           lambda s: s.hook,
    "what_happened":  lambda s: s.what_happened,
    "why_it_matters": lambda s: s.why_it_matters,
    "expert_insight": lambda s: s.expert_insight,
    "cta":            lambda s: s.cta,
}


# ── FFmpeg helpers ──────────────────────────────────────────
def ensure_assets() -> None:
    if not INTRO_PATH.exists():
        logger.warning("assets/intro.mp4 not found")
    else:
        logger.info("WNX24 intro found — ready to use")


def ffmpeg_available() -> bool:
    """Return True if the bundled FFmpeg binary can be executed."""
    try:
        result = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def _ff_escape(path: str) -> str:
    """Escape a filesystem path for use inside an ffmpeg filtergraph
    option value (drawtext fontfile=/textfile=)."""
    return str(path).replace("\\", "/").replace(":", r"\:")


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [FFMPEG, "-y", "-loglevel", "error"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-1500:]}")


def probe_duration(path: str) -> float:
    """Return media duration in seconds by parsing ffmpeg's stderr."""
    result = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr)
    if not m:
        return 0.0
    h, mins, secs, cs = (int(g) for g in m.groups())
    return h * 3600 + mins * 60 + secs + cs / 100.0


# ── Pillow drawing helpers ─────────────────────────────────
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _wrap_lines(text: str, width: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap((text or "").strip(), width=width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last  = lines[-1].rstrip()
        if len(last) >= width:
            last = last[: width - 1].rstrip()
        lines[-1] = last + "…"
    return lines


def _centred_x(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return (FRAME_W - (bbox[2] - bbox[0])) // 2


def _centred_x_box(draw: ImageDraw.ImageDraw, text: str, font, x0: int, x1: int) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return x0 + ((x1 - x0) - (bbox[2] - bbox[0])) // 2


def _base_frame() -> Image.Image:
    """Start every scene from the shared WNX24 newsroom background so the
    show-menu (left) and markets/clock (right) panels stay visible around
    scene content — falls back to a plain navy card if the asset is missing."""
    if NEWSROOM_BG.exists():
        return Image.open(NEWSROOM_BG).convert("RGB").resize((FRAME_W, FRAME_H), Image.LANCZOS)
    return Image.new("RGB", (FRAME_W, FRAME_H), BG_COLOUR)


def _draw_branding(draw: ImageDraw.ImageDraw) -> None:
    font = _font(FONT_BOLD, 36)
    draw.text((50, 30), "WNX24", fill=TICKER_COLOUR, font=font)
    draw.line([(50, 84), (FRAME_W - 50, 84)], fill=ACCENT_COLOUR, width=4)


def _draw_label_badge(draw: ImageDraw.ImageDraw, label: str) -> None:
    """Draw the section badge (e.g. "BREAKING") anchored to the top-left
    of the content box — the newsroom background already supplies the
    persistent WNX24 top-bar branding, so scenes only need this dynamic
    per-section label."""
    font = _font(FONT_BOLD, 26)
    bbox = draw.textbbox((0, 0), label, font=font)
    badge_w = (bbox[2] - bbox[0]) + 36
    x0, y0 = CONTENT_X0, CONTENT_Y0
    draw.rectangle([x0, y0, x0 + badge_w, y0 + 40], fill=ACCENT_COLOUR)
    draw.text((x0 + 18, y0 + 7), label, fill=(255, 255, 255), font=font)


# ── Scene frame builders ───────────────────────────────────
def _build_hook_frame(headline: str) -> Image.Image:
    img  = _base_frame()
    draw = ImageDraw.Draw(img)
    _draw_label_badge(draw, SECTION_LABELS["hook"])

    font  = _font(FONT_BOLD, 34)
    lines = _wrap_lines(headline or "Breaking News from WNX24", width=16, max_lines=4)
    line_h  = 44
    total_h = len(lines) * line_h
    badge_bottom = CONTENT_Y0 + 40
    y = badge_bottom + max(10, (CONTENT_Y1 - badge_bottom - total_h) // 2)
    for line in lines:
        draw.text((_centred_x_box(draw, line, font, CONTENT_X0, CONTENT_X1), y), line, fill=TEXT_COLOUR, font=font)
        y += line_h
    return img


def _build_section_frame(section: str, headline: str, body: str) -> Image.Image:
    img  = _base_frame()
    draw = ImageDraw.Draw(img)
    _draw_label_badge(draw, SECTION_LABELS.get(section, section.upper()))

    # Story headline strip for context
    head_font = _font(FONT_REG, 16)
    head_line = _wrap_lines(headline, width=50, max_lines=1)[0]
    draw.text((CONTENT_X0, CONTENT_Y0 + 46), head_line, fill=(190, 195, 215), font=head_font)

    # Body text
    body_font = _font(FONT_REG, 20)
    body_top, line_h = CONTENT_Y0 + 80, 28
    max_lines = max(1, (CONTENT_Y1 - body_top) // line_h)
    lines = _wrap_lines(body, width=36, max_lines=max_lines)
    y = body_top
    for line in lines:
        draw.text((CONTENT_X0, y), line, fill=TEXT_COLOUR, font=body_font)
        y += line_h

    return img


def _build_intro_frame(headline: str) -> Image.Image:
    img  = _base_frame()
    draw = ImageDraw.Draw(img)

    logo_font = _font(FONT_BOLD, 100)
    draw.text((_centred_x(draw, "WNX24", logo_font), 130), "WNX24", fill=TICKER_COLOUR, font=logo_font)

    sub_font = _font(FONT_BOLD, 34)
    sub = "WORLD NEWS EXPRESS 24"
    draw.text((_centred_x(draw, sub, sub_font), 270), sub, fill=TEXT_COLOUR, font=sub_font)

    draw.line([(FRAME_W // 2 - 220, 330), (FRAME_W // 2 + 220, 330)], fill=ACCENT_COLOUR, width=4)

    tagline_font = _font(FONT_REG, 28)
    draw.text(
        (_centred_x(draw, config.channel.tagline, tagline_font), 360),
        config.channel.tagline, fill=ACCENT_COLOUR, font=tagline_font,
    )

    head_font = _font(FONT_REG, 26)
    for i, line in enumerate(_wrap_lines(headline, width=52, max_lines=2)):
        draw.text((_centred_x(draw, line, head_font), 440 + i * 38), line, fill=TEXT_COLOUR, font=head_font)

    return img


def _build_outro_frame(sign_off: str) -> Image.Image:
    img  = _base_frame()
    draw = ImageDraw.Draw(img)
    _draw_branding(draw)

    font  = _font(FONT_BOLD, 40)
    lines = _wrap_lines(sign_off or config.anchor.sign_off, width=32, max_lines=4)
    line_h  = 56
    total_h = len(lines) * line_h
    y = (FRAME_H - 84 - 60 - total_h) // 2 + 84
    for line in lines:
        draw.text((_centred_x(draw, line, font), y), line, fill=TEXT_COLOUR, font=font)
        y += line_h

    handle_font = _font(FONT_REG, 26)
    handle = f"{config.channel.handle}  —  {config.channel.tagline}"
    draw.text((_centred_x(draw, handle, handle_font), y + 20), handle, fill=ACCENT_COLOUR, font=handle_font)

    return img


# ── Clip assembly ───────────────────────────────────────────
def _image_audio_clip(image_path: str, audio_path: str, out_path: str) -> None:
    _run_ffmpeg([
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-r", str(FPS), "-shortest",
        out_path,
    ])


def _image_silent_clip(image_path: str, duration: float, out_path: str) -> None:
    _run_ffmpeg([
        "-loop", "1", "-i", image_path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264", "-tune", "stillimage", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-r", str(FPS), "-t", str(duration),
        out_path,
    ])


def _concat_clips(clip_paths: list[str], out_path: str, tmp_dir: str) -> None:
    list_path = os.path.join(tmp_dir, "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")

    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ])


def _concat_segments(video_path: str, out_path: str, tmp_dir: str) -> None:
    """Prepend assets/intro.mp4 (and optional assets/outro.mp4) around the main video."""
    if not INTRO_PATH.exists():
        import shutil as _shutil
        _shutil.copy2(video_path, out_path)
        return

    segments = [str(INTRO_PATH), video_path]
    if OUTRO_PATH.exists():
        segments.append(str(OUTRO_PATH))

    list_path = os.path.join(tmp_dir, "segments_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in segments:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")

    try:
        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy",
            out_path,
        ])
    except RuntimeError:
        logger.warning("concat -c copy failed (format mismatch) — re-encoding segments")
        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            out_path,
        ])


def _clean_headline(text: str, max_len: int = 80) -> str:
    """Strip characters that break ffmpeg drawtext and cap length."""
    text = _TICKER_STRIP_RE.sub("", (text or "").strip())
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def get_ticker_headlines(limit: int = 20, hours: int = 12) -> str:
    """Build the scrolling ticker string from recent verified/broadcast
    stories. Falls back to a static WNX24 slug if the DB is empty,
    has no recent stories, or can't be reached."""
    try:
        from core.database import get_conn

        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        conn = get_conn()
        rows = conn.execute("""
            SELECT title FROM stories
            WHERE status IN ('verified', 'broadcast')
            AND published_at >= ?
            ORDER BY confidence DESC, published_at DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"get_ticker_headlines: DB query failed: {e}")
        return TICKER_FALLBACK

    headlines = [_clean_headline(row["title"]) for row in rows]
    headlines = [h for h in headlines if h]
    if not headlines:
        return TICKER_FALLBACK

    return "►  " + "  ●  ".join(headlines)


def _add_ticker(in_path: str, out_path: str, ticker_text: str, tmp_dir: str) -> None:
    ticker_file = os.path.join(tmp_dir, "ticker.txt")
    with open(ticker_file, "w", encoding="utf-8") as f:
        f.write(f"{ticker_text}          ")

    bar_h   = 64           # total ticker bar height (px)
    logo_h  = 56           # logo height — fills bar edge-to-edge (square icon = 56×56)
    logo_x  = 0            # logo flush with left edge
    div_x   = logo_h + 8  # divider x (8px gap after logo)
    font_sz = 24           # ticker headline font size
    # vertically centre text within bar
    text_y  = bar_h // 2 + font_sz // 2 + 2
    # logo fills full bar height — no vertical offset needed
    logo_y_offset = (bar_h - logo_h) // 2

    logo_esc = _ff_escape(TICKER_LOGO)
    font_esc = _ff_escape(FONT_REG)
    txt_esc  = _ff_escape(ticker_file)

    # Stage 1 — load and scale logo with Lanczos for sharpness; keep original
    # black background so it reads as a crisp dark badge against the red bar
    logo_chain = (
        f"movie='{logo_esc}',"
        f"scale={logo_h}:{logo_h}:flags=lanczos"
        f"[logo]"
    )

    # Stage 2 — draw the red bar + border + divider + scrolling text onto main
    main_chain = (
        f"[0:v]"
        f"drawbox=x=0:y=ih-{bar_h}:w=iw:h={bar_h}:color=0xe63946@0.95:t=fill,"
        f"drawbox=x=0:y=ih-{bar_h+2}:w=iw:h=2:color=white@1.0:t=fill,"
        f"drawbox=x={div_x}:y=ih-{bar_h}:w=2:h={bar_h}:color=white@0.6:t=fill,"
        f"drawtext=fontfile='{font_esc}':"
        f"textfile='{txt_esc}':reload=0:"
        f"fontcolor=white:fontsize={font_sz}:"
        f"x=w-mod(t*{TICKER_SPEED}\\,w+tw):y=h-{text_y}"
        f"[vbase]"
    )

    # Stage 3 — overlay logo flush to the left edge of the bar
    overlay_chain = (
        f"[vbase][logo]overlay="
        f"x={logo_x}:y=main_h-{bar_h}+{logo_y_offset}"
    )

    filter_complex = f"{logo_chain};{main_chain};{overlay_chain}"

    _run_ffmpeg([
        "-i", in_path,
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ])


# ── Main entry point ───────────────────────────────────────
def render_broadcast(
    script:        VideoScript,
    section_audio: dict[str, str],
    out_path:      str,
) -> float:
    """Assemble a multi-scene WNX24 broadcast video from per-section
    narration audio. Returns the final video duration in seconds."""

    ensure_assets()

    if not section_audio:
        raise ValueError("render_broadcast: no section audio provided")

    headline = (script.hook or "Breaking News from WNX24").strip()
    out_dir  = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="_wnx24_render_", dir=out_dir)

    try:
        clips: list[str] = []

        # Intro: fall back to programmatic card when assets/intro.mp4 is absent
        if not INTRO_PATH.exists():
            intro_png = os.path.join(tmp_dir, "intro.png")
            intro_card = os.path.join(tmp_dir, "intro_card.mp4")
            _build_intro_frame(headline).save(intro_png)
            _image_silent_clip(intro_png, INTRO_DURATION, intro_card)
            clips.append(intro_card)

        # One scene per scripted section with narration
        for section in SECTION_ORDER:
            audio_path = section_audio.get(section)
            if not audio_path or not os.path.exists(audio_path):
                continue

            text = (SECTION_TEXT[section](script) or "").strip()
            png_path = os.path.join(tmp_dir, f"{section}.png")
            mp4_path = os.path.join(tmp_dir, f"{section}.mp4")

            if section == "hook":
                frame = _build_hook_frame(headline)
            else:
                frame = _build_section_frame(section, headline, text)
            frame.save(png_path)

            _image_audio_clip(png_path, audio_path, mp4_path)
            clips.append(mp4_path)

        # Outro card (silent)
        outro_png = os.path.join(tmp_dir, "outro.png")
        outro_mp4 = os.path.join(tmp_dir, "outro_card.mp4")
        _build_outro_frame(script.cta).save(outro_png)
        _image_silent_clip(outro_png, OUTRO_DURATION, outro_mp4)
        clips.append(outro_mp4)

        # Concatenate scenes
        concat_path = os.path.join(tmp_dir, "concat.mp4")
        _concat_clips(clips, concat_path, tmp_dir)

        # Burn in scrolling news ticker
        ticker_text = get_ticker_headlines()
        tickered_path = os.path.join(tmp_dir, "tickered.mp4")
        _add_ticker(concat_path, tickered_path, ticker_text, tmp_dir)

        # Prepend assets/intro.mp4 (and optional assets/outro.mp4)
        _concat_segments(tickered_path, out_path, tmp_dir)

        duration = probe_duration(out_path)
        logger.info(f"Broadcast video assembled — {out_path} ({duration:.1f}s, {len(clips)} scenes)")
        return duration

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
