"""broadcast_os/render_engine.py — deterministic rendering pipeline.

NOT an AI agent — a fixed sequence of frame-blend + ffmpeg operations,
run identically every time for the same inputs (same classification as
agents/avatar_agent.py and agents/thumbnail_agent.py in this codebase).

Consumes the per-scene RGB frame arrays already produced by
broadcast_os/scenes/*.py (via BroadcastOrchestrator) plus a
BroadcastTimeline for bookkeeping, and produces the final MP4.

Frame arrays are passed alongside the timeline rather than embedded in it —
a Pydantic model is the wrong place for tens of thousands of raw pixel
arrays; BroadcastTimeline stays a clean, loggable/serialisable record of
*what* was rendered, while this function carries the *pixels*.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from loguru import logger

from broadcast_os.config import CONFIG
from broadcast_os.graphics.transitions import TransitionEngine
from broadcast_os.interfaces import BroadcastTimeline

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
INTRO_PATH = ASSETS_DIR / "intro.mp4"


@dataclass
class SceneRenderResult:
    scene_index: int
    scene_type: str
    frames: Iterable[np.ndarray]  # a generator — never a materialised list
    duration: float
    transition_in: str
    audio_path: str = ""


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [FFMPEG, "-y", "-loglevel", "error"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-1500:]}")


def _silence_clip(duration: float, out_path: str) -> str:
    _run_ffmpeg([
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(max(0.05, duration)),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ])
    return out_path


def _fit_audio_clip(src_path: str, duration: float, out_path: str) -> str:
    """Trim or silence-pad `src_path` to exactly `duration` seconds."""
    _run_ffmpeg([
        "-i", src_path,
        "-af", f"apad=whole_dur={duration}",
        "-t", str(max(0.05, duration)),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ])
    return out_path


def _concat_audio(clip_paths: list[str], out_path: str, tmp_dir: str) -> None:
    list_path = os.path.join(tmp_dir, "audio_concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ])


class RenderEngine:
    """
    Pipeline:
    1. Each scene's frames are already rendered (broadcast_os/scenes/*.py)
    2. Blend transitions between adjacent scenes (TransitionEngine)
    3. Stream the concatenated frame sequence into ffmpeg (rawvideo pipe)
    4. Build a matching audio track (per-section narration + silence
       during transitions/silent scenes)
    5. Mux video + audio into the final MP4

    Output: 1920x1080, 30fps, H264 (libx264), CRF 18, AAC audio, yuv420p.
    No background music track — no music asset exists in this codebase
    (assets/ has no music/ directory); adding a fake one would be exactly
    the kind of placeholder this build avoids.
    """

    def __init__(self, config=CONFIG):
        self.config = config

    def render(
        self,
        timeline: BroadcastTimeline,
        scenes: list[SceneRenderResult],
        output_path: str,
    ) -> str:
        if not scenes:
            raise ValueError("RenderEngine.render: no scenes to render")

        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        os.makedirs(out_dir, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix="_broadcastos_render_", dir=out_dir)

        try:
            video_path = os.path.join(tmp_dir, "video.mp4")
            total_frames, audio_segments = self._stream_and_encode(scenes, video_path)
            logger.info(
                f"RenderEngine — {total_frames} frames "
                f"({total_frames / self.config.fps:.1f}s @ {self.config.fps}fps)"
            )

            audio_path = os.path.join(tmp_dir, "audio.m4a")
            self._build_audio_track(audio_segments, audio_path, tmp_dir)

            content_path = os.path.join(tmp_dir, "content.mp4")
            self._mux(video_path, audio_path, content_path)

            # WNX24's own branded assets/intro.mp4 opens every broadcast —
            # the PIL-rendered intro_scene stays in the codebase as a
            # fallback for when that asset is missing, not the default.
            self._prepend_intro(content_path, output_path, tmp_dir)

            logger.info(f"RenderEngine complete — {output_path}")
            return output_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Steps 1-3 combined: blend transitions and stream straight into
    # ffmpeg's rawvideo pipe, one frame at a time. Scene durations are
    # unbounded (a long bulletin's narration can run minutes), so this
    # never materialises more than `transition_frames` frames at once —
    # holding an entire broadcast's frames in a Python list is what
    # caused the MemoryError this replaced.
    def _stream_and_encode(
        self, scenes: list[SceneRenderResult], out_path: str
    ) -> tuple[int, list[tuple[float, str]]]:
        k = self.config.transition_frames
        audio_segments: list[tuple[float, str]] = []  # (duration_secs, audio_path_or_empty)
        total_frames = 0
        pending_tail: list[np.ndarray] = []  # last <=k frames of the previous scene

        cmd = [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{self.config.width}x{self.config.height}",
            "-framerate", str(self.config.fps),
            "-i", "-",
            "-an",
            "-c:v", "libx264", "-preset", self.config.preset,
            "-crf", str(self.config.crf), "-pix_fmt", "yuv420p",
            out_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

        def _write(frame: np.ndarray) -> None:
            nonlocal total_frames
            proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
            total_frames += 1

        try:
            for i, scene in enumerate(scenes):
                gen: Iterator[np.ndarray] = iter(scene.frames)
                head = list(islice(gen, k))  # may be shorter than k for a very brief scene
                if not head and not pending_tail:
                    continue

                if i > 0 and pending_tail and head:
                    # The blend consumes this scene's own head frames (and
                    # the previous scene's tail) — it reshapes the cut, it
                    # doesn't add extra runtime, so no separate audio
                    # segment is needed here; `scene.duration` below
                    # already accounts for every frame this scene
                    # contributes, blended or not.
                    overlap = min(len(pending_tail), len(head))
                    for j in range(overlap):
                        _write(TransitionEngine.apply(
                            scene.transition_in, pending_tail[j], head[j], (j + 1) / overlap,
                        ))
                    for frame in head[overlap:]:
                        _write(frame)
                else:
                    for frame in head:
                        _write(frame)

                # Stream the rest of the scene, holding back the last k
                # frames as the new pending tail for the next transition.
                tail_buffer: list[np.ndarray] = []
                for frame in gen:
                    tail_buffer.append(frame)
                    if len(tail_buffer) > k:
                        _write(tail_buffer.pop(0))

                pending_tail = tail_buffer if tail_buffer else head[-k:]
                audio_segments.append((scene.duration, scene.audio_path))

            # The last scene's held-back tail was never consumed by a
            # following transition — flush it now.
            for frame in pending_tail:
                _write(frame)
        finally:
            proc.stdin.close()
            _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg rawvideo encode failed: {stderr.decode(errors='ignore')[-1500:]}")

        return total_frames, audio_segments

    # ── Step 4: build the full-length audio track ───────────────────
    def _build_audio_track(self, segments: list[tuple[float, str]], out_path: str, tmp_dir: str) -> None:
        clips: list[str] = []
        for idx, (duration, audio_path) in enumerate(segments):
            clip_path = os.path.join(tmp_dir, f"audio_{idx}.m4a")
            if audio_path and os.path.exists(audio_path):
                clips.append(_fit_audio_clip(audio_path, duration, clip_path))
            else:
                clips.append(_silence_clip(duration, clip_path))
        _concat_audio(clips, out_path, tmp_dir)

    # ── Step 5: mux video + audio ────────────────────────────────────
    def _mux(self, video_path: str, audio_path: str, output_path: str) -> None:
        _run_ffmpeg([
            "-i", video_path, "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ])

    # ── Step 6: prepend the branded assets/intro.mp4 opener ──────────
    def _prepend_intro(self, content_path: str, output_path: str, tmp_dir: str) -> None:
        if not INTRO_PATH.exists():
            logger.warning("assets/intro.mp4 not found — falling back to the rendered intro_scene")
            shutil.copy2(content_path, output_path)
            return

        segments = [str(INTRO_PATH), content_path]
        inputs: list[str] = []
        for p in segments:
            inputs += ["-i", p]

        n = len(segments)
        parts, joins = [], []
        for i in range(n):
            parts.append(
                f"[{i}:v]scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=decrease,"
                f"pad={self.config.width}:{self.config.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"fps={self.config.fps},format=yuv420p[v{i}]"
            )
            parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
            joins.append(f"[v{i}][a{i}]")
        filter_complex = ";".join(parts) + ";" + "".join(joins) + f"concat=n={n}:v=1:a=1[outv][outa]"

        try:
            _run_ffmpeg(inputs + [
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-crf", str(self.config.crf), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart",
                output_path,
            ])
        except RuntimeError as e:
            logger.warning(f"Could not prepend assets/intro.mp4 ({e}) — using content without it")
            shutil.copy2(content_path, output_path)
