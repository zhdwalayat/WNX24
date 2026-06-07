import os
import asyncio
import textwrap
import uuid
from pathlib import Path
from loguru import logger

from core.models import VideoScript, AvatarVideo, VideoStatus
from config.settings import config

# ── Directories ────────────────────────────────────────────
ASSETS_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "assets"
FONTS_DIR  = ASSETS_DIR / "fonts"
FONT_BOLD  = str(FONTS_DIR / "DejaVuSansMono-Bold.ttf")
FONT_REG   = str(FONTS_DIR / "DejaVuSansMono.ttf")

# ── Edge-TTS voice ─────────────────────────────────────────
TTS_VOICE  = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")   # free Microsoft neural voice

# ── Colour palette ─────────────────────────────────────────
BG_COLOUR      = (10,  12,  40)   # dark navy
ACCENT_COLOUR  = (220, 50,  50)   # red ticker
TEXT_COLOUR    = (240, 240, 240)  # near-white
TICKER_COLOUR  = (255, 200,  40)  # gold headline


# ── Step 1: Text → speech (edge-tts, fully free) ──────────
async def _tts_async(text: str, out_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(out_path)


_TTS_LOCK_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "output", "tts.lock")


def _acquire_tts_lock(timeout: int = 120) -> None:
    """Busy-wait until no other process holds the TTS lock file."""
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            fd = os.open(_TTS_LOCK_FILE,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return                          # lock acquired
        except FileExistsError:
            _time.sleep(0.5)               # another process holds it
    raise RuntimeError("Could not acquire TTS lock after timeout")


def _release_tts_lock() -> None:
    try:
        os.remove(_TTS_LOCK_FILE)
    except FileNotFoundError:
        pass


def _text_to_speech(text: str, out_path: str, retries: int = 3) -> None:
    import time as _time

    _acquire_tts_lock()
    try:
        for attempt in range(1, retries + 1):
            try:
                asyncio.run(_tts_async(text, out_path))
                if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                    logger.info(f"TTS saved → {out_path} ({os.path.getsize(out_path)} bytes)")
                    return
                logger.warning(f"TTS attempt {attempt}: empty file, retrying...")
            except Exception as e:
                logger.warning(f"TTS attempt {attempt} error: {e}")
            _time.sleep(3 * attempt)
        raise RuntimeError(f"TTS failed after {retries} attempts")
    finally:
        _release_tts_lock()


# ── Step 2: Build news-anchor frame with Pillow ────────────
def _build_frame(headline: str, subtext: str, frame_w: int, frame_h: int) -> "Image":
    from PIL import Image, ImageDraw, ImageFont

    img  = Image.new("RGB", (frame_w, frame_h), BG_COLOUR)
    draw = ImageDraw.Draw(img)

    # ── Red ticker bar at bottom ───────────────────────────
    ticker_h = 80
    ticker_y = frame_h - ticker_h
    draw.rectangle([0, ticker_y, frame_w, frame_h], fill=ACCENT_COLOUR)

    # "BREAKING" label
    try:
        label_font = ImageFont.truetype(FONT_BOLD, 28)
    except Exception:
        label_font = ImageFont.load_default()
    draw.text((24, ticker_y + 24), "● BREAKING", fill=(255, 255, 255), font=label_font)

    # Headline on ticker
    try:
        tick_font = ImageFont.truetype(FONT_BOLD, 26)
    except Exception:
        tick_font = ImageFont.load_default()
    short_hl = headline[:80] + ("…" if len(headline) > 80 else "")
    draw.text((240, ticker_y + 27), short_hl, fill=(255, 255, 230), font=tick_font)

    # ── Channel name top-left ──────────────────────────────
    try:
        ch_font = ImageFont.truetype(FONT_BOLD, 36)
    except Exception:
        ch_font = ImageFont.load_default()
    draw.text((40, 40), "MERIDIAN NEWS NETWORK", fill=TICKER_COLOUR, font=ch_font)
    draw.line([(40, 86), (frame_w - 40, 86)], fill=ACCENT_COLOUR, width=3)

    # ── Anchor placeholder silhouette ─────────────────────
    # Simple geometric anchor stand-in
    cx, cy = frame_w // 2, frame_h // 2 - 30
    # head
    draw.ellipse([cx - 70, cy - 160, cx + 70, cy - 20], fill=(80, 90, 120))
    # body
    draw.rounded_rectangle([cx - 110, cy - 20, cx + 110, cy + 180],
                            radius=30, fill=(60, 70, 100))
    # podium / desk
    draw.rectangle([cx - 220, cy + 160, cx + 220, cy + 220], fill=(40, 45, 80))

    # ── Story subtext below anchor ─────────────────────────
    try:
        body_font = ImageFont.truetype(FONT_REG, 24)
    except Exception:
        body_font = ImageFont.load_default()

    wrapped = textwrap.fill(subtext[:180], width=70)
    lines   = wrapped.split("\n")
    y_text  = cy + 240
    for line in lines[:3]:
        bbox = draw.textbbox((0, 0), line, font=body_font)
        tw   = bbox[2] - bbox[0]
        draw.text(((frame_w - tw) // 2, y_text), line,
                  fill=TEXT_COLOUR, font=body_font)
        y_text += 34

    return img


# ── Step 3: Image + audio → MP4 ───────────────────────────
def _build_video(audio_path: str, headline: str, subtext: str,
                 out_path: str, frame_w: int = 1280, frame_h: int = 720) -> None:
    import imageio_ffmpeg
    import os as _os
    # Point moviepy to bundled ffmpeg so no system install is needed
    _os.environ["FFMPEG_BINARY"] = imageio_ffmpeg.get_ffmpeg_exe()

    from moviepy.editor import AudioFileClip, ImageClip

    frame = _build_frame(headline, subtext, frame_w, frame_h)
    frame_path = out_path.replace(".mp4", "_frame.png")
    frame.save(frame_path)

    audio   = AudioFileClip(audio_path)
    clip    = ImageClip(frame_path).set_duration(audio.duration).set_audio(audio)
    clip.write_videofile(
        out_path,
        fps         = 24,
        codec       = "libx264",
        audio_codec = "aac",
        verbose     = False,
        logger      = None,
    )

    # Clean up temp frame
    try:
        os.remove(frame_path)
    except Exception:
        pass

    logger.info(f"Video built → {out_path} ({audio.duration:.1f}s)")


# ── Main: generate local video ─────────────────────────────
def generate_local_video(script: VideoScript) -> AvatarVideo:
    job_id     = script.story_id or uuid.uuid4().hex[:12]
    output_dir = Path(config.output_dir) / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = str(output_dir / f"{job_id}.mp3")
    video_path = str(output_dir / f"{job_id}.mp4")

    # 1. TTS
    logger.info(f"TTS — {len(script.full_text)} chars via edge-tts ({TTS_VOICE})")
    try:
        _text_to_speech(script.full_text, audio_path)
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return AvatarVideo(script=script, status=VideoStatus.FAILED)

    # 2. Video
    headline = script.hook or "Breaking News"
    subtext  = script.what_happened or ""
    logger.info("Compositing news-anchor frame + audio...")
    try:
        _build_video(audio_path, headline, subtext, video_path)
    except Exception as e:
        logger.error(f"Video build failed: {e}")
        return AvatarVideo(script=script, status=VideoStatus.FAILED)

    # 3. Clean up audio
    try:
        os.remove(audio_path)
    except Exception:
        pass

    return AvatarVideo(
        script        = script,
        local_path    = video_path,
        heygen_job_id = job_id,
        duration_secs = script.duration_secs,
        status        = VideoStatus.RENDERED,
    )


# ── Main agent function ────────────────────────────────────
def run_avatar_agent(script: VideoScript) -> AvatarVideo:
    logger.info("Starting Avatar Agent — Meridian News Network (local TTS + video)")

    video = generate_local_video(script)

    logger.info(
        f"Avatar Agent complete — "
        f"Status: {video.status} | "
        f"Path: {video.local_path}"
    )
    return video
