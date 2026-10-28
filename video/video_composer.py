# video/video_composer.py
"""Broadcast video composer — replaces the HeyGen/avatar pipeline with
professional image + voice composition: Creative-Commons imagery (Ken
Burns effect) narrated by edge-tts, laid over a WNX24 newsroom
background, with lower thirds, a scrolling ticker and intro/outro.
"""
import itertools
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from config.settings import config
from core.models import AvatarVideo, VerifiedStory, VideoScript, VideoStatus
from video.video_editor import get_ticker_headlines, probe_duration

# ── Paths ──────────────────────────────────────────────────
ROOT_DIR   = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
FONT_BOLD  = str(ASSETS_DIR / "fonts" / "DejaVuSansMono-Bold.ttf")
FONT_REG   = str(ASSETS_DIR / "fonts" / "DejaVuSansMono.ttf")
GLOBE_LOGO = ASSETS_DIR / "WNX24_logo_3_globe.png"

NEWSROOM_BG_JPG = ASSETS_DIR / "newsroom_bg.jpg"
NEWSROOM_BG_PNG = ASSETS_DIR / "newsroom_bg.png"

INTRO_PATH = ASSETS_DIR / "intro.mp4"
OUTRO_PATH = ASSETS_DIR / "outro.mp4"

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ── Frame / encode settings ────────────────────────────────
FRAME_W, FRAME_H = 1920, 1080
FPS              = 24

# Content box within the 1920x1080 newsroom background where story
# imagery is composited — leaves the left show-menu panel, right
# markets/clocks panel, and bottom ticker gutter all visible around it.
CONTENT_X0, CONTENT_Y0 = 220, 80
CONTENT_X1, CONTENT_Y1 = 1330, 630
CONTENT_W = CONTENT_X1 - CONTENT_X0
CONTENT_H = CONTENT_Y1 - CONTENT_Y0
TICKER_HEIGHT = 54
KEN_BURNS_EFFECTS = ["zoom_in", "zoom_out", "pan_left", "pan_right"]

SECTION_LABELS = {
    "hook":           "BREAKING",
    "what_happened":  "WHAT HAPPENED",
    "why_it_matters": "WHY IT MATTERS",
    "expert_insight": "EXPERT INSIGHT",
    "cta":            "SIGN OFF",
}


# ── FFmpeg helpers ──────────────────────────────────────────
def _run_ffmpeg(args: list[str]) -> None:
    cmd = [FFMPEG, "-y", "-loglevel", "error"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-1500:]}")


def _ff_escape(path: str) -> str:
    """Escape a filesystem path for use inside an ffmpeg filtergraph
    option value (drawtext fontfile=/movie=)."""
    return str(path).replace("\\", "/").replace(":", r"\:")


def _dt_escape(text: str) -> str:
    """Escape literal text for a drawtext `text=` filter option."""
    text = (text or "").replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")
    return re.sub(r"[\r\n]+", " ", text)


def _clean(text: str, max_len: int = 90) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


# ── Part 4: professional newsroom background (PIL) ─────────
def build_newsroom_background_image() -> Image.Image:
    """Render the static WNX24 newsroom background: navy gradient, left
    logo panel, subtle grid/scan lines, top and bottom bars, corner
    accent dots and a faint globe watermark."""
    W, H = FRAME_W, FRAME_H
    top_colour    = (0x06, 0x09, 0x10)
    bottom_colour = (0x0d, 0x1b, 0x35)

    base = Image.new("RGB", (W, H), top_colour)
    draw = ImageDraw.Draw(base)
    for y in range(H):
        t   = y / (H - 1)
        row = tuple(int(top_colour[c] + (bottom_colour[c] - top_colour[c]) * t) for c in range(3))
        draw.line([(0, y), (W, y)], fill=row)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw   = ImageDraw.Draw(overlay)

    # Left panel — slightly lighter navy, holds the WNX24 logo
    odraw.rectangle([0, 0, 480, H], fill=(40, 60, 110, 55))

    # Subtle grid overlay (opacity ~0.03)
    for x in range(0, W, 80):
        odraw.line([(x, 0), (x, H)], fill=(120, 150, 210, 8))
    for y in range(0, H, 80):
        odraw.line([(0, y), (W, y)], fill=(120, 150, 210, 8))

    # Broadcast-TV scan lines (opacity ~0.03)
    for y in range(0, H, 3):
        odraw.line([(0, y), (W, y)], fill=(0, 0, 0, 8))

    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    # Right-side globe watermark (opacity ~0.05)
    if GLOBE_LOGO.exists():
        try:
            globe  = Image.open(GLOBE_LOGO).convert("RGBA")
            gw     = 460
            ratio  = gw / globe.width
            gh     = int(globe.height * ratio)
            globe  = globe.resize((gw, gh), Image.LANCZOS)
            alpha  = globe.split()[3].point(lambda p: int(p * 0.05))
            globe.putalpha(alpha)
            base.paste(globe, (W - gw - 40, (H - gh) // 2), globe)
        except Exception as e:
            logger.warning(f"Could not paste globe watermark: {e}")

    draw = ImageDraw.Draw(base)

    # Left red accent bar, 8px wide
    draw.rectangle([0, 0, 8, H], fill=(230, 57, 70))

    # Top WNX24 bar, 40px
    draw.rectangle([0, 0, W, 40], fill=(10, 15, 30))
    top_font = _font(FONT_BOLD, 24)
    draw.text((24, 8), "WNX24", fill=(255, 255, 255), font=top_font)
    date_font = _font(FONT_REG, 18)
    draw.text((W - 260, 10), "LIVE  |  --:-- PKT", fill=(190, 195, 215), font=date_font)

    # Bottom ticker area reserved, 36px
    draw.rectangle([0, H - 36, W, H], fill=(230, 57, 70))

    # Corner accent dots, electric blue
    dot_colour = (30, 144, 255)
    r = 6
    for cx, cy in [(24, 64), (W - 24, 64), (24, H - 60), (W - 24, H - 60)]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot_colour)

    # WNX24 wordmark in the left logo panel
    logo_font = _font(FONT_BOLD, 64)
    draw.text((40, H // 2 - 40), "WNX24", fill=(255, 209, 0), font=logo_font)
    tagline_font = _font(FONT_REG, 20)
    draw.text((40, H // 2 + 34), config.channel.tagline, fill=(200, 60, 70), font=tagline_font)

    return base


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def generate_newsroom_background_assets() -> None:
    """Render and save assets/newsroom_bg.jpg and assets/newsroom_bg.png."""
    img = build_newsroom_background_image()
    img.save(NEWSROOM_BG_JPG, "JPEG", quality=92)
    img.save(NEWSROOM_BG_PNG, "PNG")
    logger.info(f"Newsroom background saved — {NEWSROOM_BG_JPG}, {NEWSROOM_BG_PNG}")


def _newsroom_bg_path(tmp_dir: str) -> str:
    if NEWSROOM_BG_JPG.exists():
        return str(NEWSROOM_BG_JPG)
    # Fallback: flat navy gradient generated on the fly via ffmpeg lavfi
    logger.warning("assets/newsroom_bg.jpg not found — using plain navy fallback")
    fallback = os.path.join(tmp_dir, "bg_fallback.png")
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=0x0d1b35:s={FRAME_W}x{FRAME_H}:d=1",
        "-frames:v", "1", fallback,
    ])
    return fallback


# ── Part 3.1: Ken Burns effect on a single image ────────────
def apply_ken_burns(
    image_path: str,
    output_path: str,
    duration: float,
    effect: str = "zoom_in",
    width: int = FRAME_W,
    height: int = FRAME_H,
) -> str:
    """Render `image_path` into a silent MP4 clip of `duration` seconds
    with a Ken Burns pan/zoom effect applied."""
    duration = max(0.5, float(duration))
    frames   = max(1, round(duration * FPS))

    # Zoom step is sized to *this clip's* frame count so the effect completes
    # exactly at the end of the clip — a fixed per-frame step reaches its cap
    # early on longer clips and then freezes at max zoom for the remainder.
    zoom_max  = 1.12
    zoom_step = (zoom_max - 1.0) / frames

    if effect == "zoom_in":
        z_expr = f"min(zoom+{zoom_step:.8f},{zoom_max})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == "zoom_out":
        z_expr = f"if(eq(on,1),{zoom_max},max(zoom-{zoom_step:.8f},1.0))"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == "pan_left":
        z_expr = "1.15"
        x_expr = f"(iw-iw/zoom)*on/{frames}"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == "pan_right":
        z_expr = "1.15"
        x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        raise ValueError(f"Unknown Ken Burns effect: {effect}")

    # Upscale 2x before zoompan so the crop never runs out of source detail
    vf = (
        f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
        f"crop={width*2}:{height*2},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={FPS},"
        f"format=yuv420p"
    )

    _run_ffmpeg([
        # -framerate here is required: without it the image2 demuxer's
        # default timing doesn't feed zoompan new frames properly, so the
        # filter freezes on frame 1 for the whole clip instead of animating.
        "-loop", "1", "-framerate", str(FPS), "-i", image_path,
        "-vf", vf, "-t", str(duration), "-r", str(FPS),
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        output_path,
    ])
    return output_path


# ── Part 3.3: composite story imagery onto the newsroom bg ──
def add_newsroom_background(clip_path: str, output_path: str, tmp_dir: str | None = None) -> str:
    """Composite `clip_path` (story imagery, already sized to the content
    box) onto the WNX24 newsroom background at the fixed content-box
    position — leaves the left show-menu panel, right markets/clocks
    panel, and bottom ticker gutter visible around it."""
    own_tmp = tmp_dir is None
    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="_wnx24_bg_")
    try:
        bg_path = _newsroom_bg_path(tmp_dir)

        filter_complex = (
            f"[0:v]scale={FRAME_W}:{FRAME_H}[bg];"
            f"[1:v]scale={CONTENT_W}:{CONTENT_H}[fg];"
            f"[bg][fg]overlay=x={CONTENT_X0}:y={CONTENT_Y0}:shortest=1,format=yuv420p[v]"
        )
        _run_ffmpeg([
            "-loop", "1", "-i", bg_path,
            "-i", clip_path,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
            output_path,
        ])
        return output_path
    finally:
        if own_tmp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Lower third + attribution burn-in ───────────────────────
def _add_lower_third(in_path: str, out_path: str, section_name: str, story: VerifiedStory) -> None:
    label       = _dt_escape(SECTION_LABELS.get(section_name, section_name.upper()))
    attribution = _dt_escape(f"Source: {_clean(story.raw.source_name, 40)}")

    # Sits on the bottom edge of the content box (standard lower-third
    # placement, over the imagery itself) — stays clear of the ticker
    # gutter below CONTENT_Y1.
    bar_h = 90
    bar_y = CONTENT_Y1 - bar_h
    filter_complex = (
        f"drawbox=x={CONTENT_X0}:y={bar_y}:w={CONTENT_W}:h={bar_h}:color=0x0a0f1e@0.78:t=fill,"
        f"drawbox=x={CONTENT_X0}:y={bar_y}:w=340:h={bar_h}:color=0xe63946@0.92:t=fill,"
        f"drawtext=fontfile='{_ff_escape(FONT_BOLD)}':text='{label}':"
        f"fontcolor=white:fontsize=34:x={CONTENT_X0 + 20}:y={bar_y + 28},"
        f"drawtext=fontfile='{_ff_escape(FONT_REG)}':text='{attribution}':"
        f"fontcolor=0xd0d0d0:fontsize=24:x={CONTENT_X0 + 360}:y={bar_y + 32}"
    )
    _run_ffmpeg([
        "-i", in_path,
        "-vf", filter_complex,
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        out_path,
    ])


# ── Part 3.2: one narrated section clip ─────────────────────
def create_section_clip(
    image_paths: list[str],
    audio_path:  str,
    duration:    float,
    section_name: str,
    story:       VerifiedStory,
    show_id:     str,
    tmp_dir:     str,
    effect_cycle,
) -> str:
    """Build one broadcast section: Ken Burns imagery composited onto
    the newsroom background, a lower third, synced to `audio_path`."""
    duration = max(0.5, float(duration))

    n_images     = max(1, min(len(image_paths), round(duration / 7) or 1))
    chosen       = [image_paths[i % len(image_paths)] for i in range(n_images)]
    per_duration = duration / n_images

    kb_clips: list[str] = []
    for i, img_path in enumerate(chosen):
        kb_path = os.path.join(tmp_dir, f"{section_name}_kb_{i}.mp4")
        apply_ken_burns(
            img_path, kb_path, per_duration,
            effect=next(effect_cycle), width=CONTENT_W, height=CONTENT_H,
        )
        kb_clips.append(kb_path)

    if len(kb_clips) == 1:
        inset_silent = kb_clips[0]
    else:
        inset_silent = os.path.join(tmp_dir, f"{section_name}_inset.mp4")
        _concat_same_format(kb_clips, inset_silent, tmp_dir)

    composited = os.path.join(tmp_dir, f"{section_name}_composited.mp4")
    add_newsroom_background(inset_silent, composited, tmp_dir)

    muxed = os.path.join(tmp_dir, f"{section_name}_muxed.mp4")
    _run_ffmpeg([
        "-i", composited, "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-shortest", muxed,
    ])

    final_path = os.path.join(tmp_dir, f"{section_name}_final.mp4")
    _add_lower_third(muxed, final_path, section_name, story)
    return final_path


# ── Concat helpers ──────────────────────────────────────────
def _concat_same_format(clip_paths: list[str], out_path: str, tmp_dir: str) -> None:
    """Concatenate clips that already share codec/resolution/fps (all
    produced by this module) — fast stream copy, no re-encode."""
    list_path = os.path.join(tmp_dir, f"concat_{os.path.basename(out_path)}.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path])


def _concat_with_scaling(segment_paths: list[str], out_path: str) -> None:
    """Concatenate clips of differing resolution/fps (e.g. assets/intro.mp4
    authored at a different size) by scaling/padding each to the composer's
    target frame before joining — avoids the concat-demuxer resolution
    mismatch that a naive `-c copy` concat would produce."""
    inputs = []
    for p in segment_paths:
        inputs += ["-i", p]

    n = len(segment_paths)
    parts, joins = [], []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
            f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{i}]"
        )
        parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
        joins.append(f"[v{i}][a{i}]")

    filter_complex = ";".join(parts) + ";" + "".join(joins) + f"concat=n={n}:v=1:a=1[outv][outa]"

    _run_ffmpeg(inputs + [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ])


# ── Ticker + permanent WNX24 lower-third bar ────────────────
def _add_ticker_and_brand_bar(in_path: str, out_path: str, ticker_text: str, tmp_dir: str) -> None:
    ticker_file = os.path.join(tmp_dir, "ticker.txt")
    with open(ticker_file, "w", encoding="utf-8") as f:
        f.write(f"{ticker_text}          ")

    speed   = 110
    font_sz = 26
    text_y  = TICKER_HEIGHT // 2 + font_sz // 2

    filter_complex = (
        f"drawbox=x=0:y=ih-{TICKER_HEIGHT}:w=iw:h={TICKER_HEIGHT}:color=0xe63946@0.95:t=fill,"
        f"drawbox=x=0:y=ih-{TICKER_HEIGHT + 2}:w=iw:h=2:color=white@1.0:t=fill,"
        f"drawtext=fontfile='{_ff_escape(FONT_REG)}':textfile='{_ff_escape(ticker_file)}':reload=0:"
        f"fontcolor=white:fontsize={font_sz}:"
        f"x=w-mod(t*{speed}\\,w+tw):y=h-{text_y},"
        f"drawbox=x=0:y=0:w=210:h=44:color=0x0a0f1e@0.85:t=fill,"
        f"drawtext=fontfile='{_ff_escape(FONT_BOLD)}':text='WNX24 LIVE':"
        f"fontcolor=0xe63946:fontsize=26:x=14:y=10"
    )

    _run_ffmpeg([
        "-i", in_path,
        "-vf", filter_complex,
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ])


# ── Main composition pipeline ───────────────────────────────
def compose_broadcast(
    script:  VideoScript,
    story:   VerifiedStory,
    show_id: str,
) -> str:
    """Assemble the full WNX24 broadcast video: narration + CC imagery
    (Ken Burns) + newsroom background + lower thirds + ticker + intro/outro.
    Returns the path to the final MP4."""
    from agents.image_agent import run_image_agent
    from agents.voice_agent import run_voice_agent

    out_dir = os.path.join(config.output_dir, "videos")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{show_id}_{script.story_id or 'broadcast'}_composed.mp4")

    tmp_dir = tempfile.mkdtemp(prefix="_wnx24_compose_", dir=out_dir)
    try:
        # Step 1 — narration audio per section
        sections = run_voice_agent(script, show_id)
        if not sections:
            raise RuntimeError("compose_broadcast: voice agent returned no sections")

        # Step 2 — Creative Commons imagery for the story
        images = run_image_agent(story, show_id, count=6)
        if not images:
            raise RuntimeError("compose_broadcast: image agent returned no images")

        # Step 3 — one Ken Burns + lower-third clip per narrated section
        effect_cycle = itertools.cycle(KEN_BURNS_EFFECTS)
        section_clips = []
        for sec in sections:
            clip = create_section_clip(
                images, sec["audio_path"], sec["duration"], sec["section"],
                story, show_id, tmp_dir, effect_cycle,
            )
            section_clips.append(clip)

        # Step 4 — concatenate sections (all same format — fast copy concat)
        main_path = os.path.join(tmp_dir, "main.mp4")
        if len(section_clips) == 1:
            shutil.copy2(section_clips[0], main_path)
        else:
            _concat_same_format(section_clips, main_path, tmp_dir)

        # Step 5 + 6 — scrolling ticker and permanent WNX24 brand bar
        ticker_text = get_ticker_headlines()
        tickered_path = os.path.join(tmp_dir, "tickered.mp4")
        _add_ticker_and_brand_bar(main_path, tickered_path, ticker_text, tmp_dir)

        # Step 7 + 8 — prepend intro / append outro (best-effort)
        segments = []
        if INTRO_PATH.exists():
            segments.append(str(INTRO_PATH))
        else:
            logger.warning("assets/intro.mp4 not found — skipping intro")
        segments.append(tickered_path)
        if OUTRO_PATH.exists():
            segments.append(str(OUTRO_PATH))

        if len(segments) == 1:
            shutil.copy2(tickered_path, out_path)
        else:
            _concat_with_scaling(segments, out_path)

        # Step 9
        return out_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Main entry point — replaces run_avatar_agent() ──────────
def run_video_composer(script: VideoScript, story: VerifiedStory, show_id: str) -> AvatarVideo:
    logger.info("Starting Video Composer — WNX24 (image + voice broadcast pipeline)")
    try:
        video_path = compose_broadcast(script, story, show_id)
        duration   = probe_duration(video_path)
        logger.info(f"Video Composer complete — {video_path} ({duration:.1f}s)")
        return AvatarVideo(
            script        = script,
            local_path    = video_path,
            duration_secs = duration,
            status        = VideoStatus.RENDERED,
        )
    except Exception as e:
        logger.error(f"Video Composer failed: {e}")
        return AvatarVideo(script=script, status=VideoStatus.FAILED)


if __name__ == "__main__":
    generate_newsroom_background_assets()
