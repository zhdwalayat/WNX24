# video/video_editor.py
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, List

import imageio_ffmpeg
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from core.models import VideoScript
from config.settings import config

# ── Paths ──────────────────────────────────────────────────
ROOT_DIR   = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
FONT_BOLD  = str(ASSETS_DIR / "fonts" / "DejaVuSansMono-Bold.ttf")
FONT_REG   = str(ASSETS_DIR / "fonts" / "DejaVuSansMono.ttf")

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ── Frame / encode settings ───────────────────────────────────
FRAME_W, FRAME_H = 1280, 720
FPS              = 24

# ── Colour palette ─────────────────────────────────────────
BG_COLOUR     = (10,  12,  40)   # dark navy
ACCENT_COLOUR = (220, 50,  50)   # red ticker / badge
TEXT_COLOUR   = (240, 240, 240)  # near-white
TICKER_COLOUR = (255, 200,  40)  # gold headline

INTRO_DURATION = 3.0
OUTRO_DURATION = 4.0
TICKER_SPEED   = 110   # px/sec scroll speed

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


def _run_ffmpeg(args: List[str]) -> None:
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
def _font(path: str, size: int) -> "ImageFont.FreeTypeFont":
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _wrap_lines(text: str, width: int, max_lines: int) -> List[str]:
    lines = textwrap.wrap((text or "").strip(), width=width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last  = lines[-1].rstrip()
        if len(last) >= width:
            last = last[: width - 1].rstrip()
        lines[-1] = last + "…"
    return lines


def _centred_x(draw: "ImageDraw.ImageDraw", text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return (FRAME_W - (bbox[2] - bbox[0])) // 2


def _draw_branding(draw: "ImageDraw.ImageDraw") -> None:
    font = _font(FONT_BOLD, 36)
    draw.text((50, 30), "MERIDIAN NEWS NETWORK", fill=TICKER_COLOUR, font=font)
    draw.line([(50, 84), (FRAME_W - 50, 84)], fill=ACCENT_COLOUR, width=4)


def _draw_ticker_bar(draw: "ImageDraw.ImageDraw") -> None:
    bar_h = 60
    draw.rectangle([0, FRAME_H - bar_h, FRAME_W, FRAME_H], fill=ACCENT_COLOUR)


def _draw_label_badge(draw: "ImageDraw.ImageDraw", label: str) -> None:
    font = _font(FONT_BOLD, 32)
    bbox = draw.textbbox((0, 0), label, font=font)
    badge_w = (bbox[2] - bbox[0]) + 50
    draw.rectangle([50, 110, 50 + badge_w, 162], fill=ACCENT_COLOUR)
    draw.text((75, 119), label, fill=(255, 255, 255), font=font)


# ── Scene frame builders ───────────────────────────────────
def _build_hook_frame(headline: str) -> "Image.Image":
    img  = Image.new("RGB", (FRAME_W, FRAME_H), BG_COLOUR)
    draw = ImageDraw.Draw(img)
    _draw_branding(draw)
    _draw_label_badge(draw, SECTION_LABELS["hook"])

    font  = _font(FONT_BOLD, 56)
    lines = _wrap_lines(headline or "Breaking News from MNN", width=22, max_lines=4)
    line_h  = 76
    total_h = len(lines) * line_h
    y = (FRAME_H - 162 - total_h) // 2 + 162
    for line in lines:
        draw.text((_centred_x(draw, line, font), y), line, fill=TEXT_COLOUR, font=font)
        y += line_h

    _draw_ticker_bar(draw)
    return img


def _build_section_frame(section: str, headline: str, body: str) -> "Image.Image":
    img  = Image.new("RGB", (FRAME_W, FRAME_H), BG_COLOUR)
    draw = ImageDraw.Draw(img)
    _draw_branding(draw)
    _draw_label_badge(draw, SECTION_LABELS.get(section, section.upper()))

    # Story headline strip for context
    head_font = _font(FONT_REG, 24)
    head_line = _wrap_lines(headline, width=82, max_lines=1)[0]
    draw.text((50, 178), head_line, fill=(190, 195, 215), font=head_font)

    # Body text
    body_font = _font(FONT_REG, 30)
    body_top, ticker_top, line_h = 236, FRAME_H - 60, 44
    max_lines = max(1, (ticker_top - body_top) // line_h)
    lines = _wrap_lines(body, width=56, max_lines=max_lines)
    y = body_top
    for line in lines:
        draw.text((50, y), line, fill=TEXT_COLOUR, font=body_font)
        y += line_h

    _draw_ticker_bar(draw)
    return img


def _build_intro_frame(headline: str) -> "Image.Image":
    img  = Image.new("RGB", (FRAME_W, FRAME_H), BG_COLOUR)
    draw = ImageDraw.Draw(img)

    logo_font = _font(FONT_BOLD, 100)
    draw.text((_centred_x(draw, "MNN", logo_font), 130), "MNN", fill=TICKER_COLOUR, font=logo_font)

    sub_font = _font(FONT_BOLD, 34)
    sub = "MERIDIAN NEWS NETWORK"
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

    _draw_ticker_bar(draw)
    return img


def _build_outro_frame(sign_off: str) -> "Image.Image":
    img  = Image.new("RGB", (FRAME_W, FRAME_H), BG_COLOUR)
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

    _draw_ticker_bar(draw)
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


def _concat_clips(clip_paths: List[str], out_path: str, tmp_dir: str) -> None:
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


def _add_ticker(in_path: str, out_path: str, ticker_text: str, tmp_dir: str) -> None:
    ticker_file = os.path.join(tmp_dir, "ticker.txt")
    with open(ticker_file, "w", encoding="utf-8") as f:
        f.write(f"{ticker_text}     •     ")

    vf = (
        f"drawtext=fontfile='{_ff_escape(FONT_BOLD)}':"
        f"textfile='{_ff_escape(ticker_file)}':reload=0:"
        f"fontcolor=white:fontsize=28:"
        f"x=w-mod(t*{TICKER_SPEED}\\,tw+w):y=h-46"
    )
    _run_ffmpeg([
        "-i", in_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ])


# ── Main entry point ───────────────────────────────────────
def render_broadcast(
    script:        VideoScript,
    section_audio: Dict[str, str],
    out_path:      str,
) -> float:
    """Assemble a multi-scene MNN broadcast video from per-section
    narration audio. Returns the final video duration in seconds."""

    if not section_audio:
        raise ValueError("render_broadcast: no section audio provided")

    headline = (script.hook or "Breaking News from MNN").strip()
    out_dir  = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="_mnn_render_", dir=out_dir)

    try:
        clips: List[str] = []

        # Intro card (silent)
        intro_png = os.path.join(tmp_dir, "intro.png")
        intro_mp4 = os.path.join(tmp_dir, "intro.mp4")
        _build_intro_frame(headline).save(intro_png)
        _image_silent_clip(intro_png, INTRO_DURATION, intro_mp4)
        clips.append(intro_mp4)

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
        outro_mp4 = os.path.join(tmp_dir, "outro.mp4")
        _build_outro_frame(script.cta).save(outro_png)
        _image_silent_clip(outro_png, OUTRO_DURATION, outro_mp4)
        clips.append(outro_mp4)

        # Concatenate scenes
        concat_path = os.path.join(tmp_dir, "concat.mp4")
        _concat_clips(clips, concat_path, tmp_dir)

        # Burn in scrolling news ticker
        ticker_text = "     •     ".join(filter(None, [
            f"MNN — {config.channel.tagline}",
            headline,
            (config.anchor.sign_off or "").strip(),
        ]))
        _add_ticker(concat_path, out_path, ticker_text, tmp_dir)

        duration = probe_duration(out_path)
        logger.info(f"Broadcast video assembled — {out_path} ({duration:.1f}s, {len(clips)} scenes)")
        return duration

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
