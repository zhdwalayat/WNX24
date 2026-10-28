"""broadcast_os/graphics/transitions.py — professional scene transitions.

All transitions take two RGB frames (np.ndarray, HxWx3 uint8) of identical
shape and a progress value t in [0, 1], and return one blended frame. The
render engine calls these once per transition frame (CONFIG.transition_frames
frames total) with t sweeping 0 -> 1.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from broadcast_os.graphics._canvas import ease_in_out


def _to_pil(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(frame.astype(np.uint8), "RGB")


def _to_np(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


class TransitionEngine:
    """cross_dissolve / zoom_in / light_sweep / digital_push / blur_fade —
    all eased with ease_in_out(t), all designed for CONFIG.transition_frames
    total frames per cut."""

    @staticmethod
    def cross_dissolve(frame_a: np.ndarray, frame_b: np.ndarray, t: float) -> np.ndarray:
        e = ease_in_out(t)
        a = frame_a.astype(np.float32)
        b = frame_b.astype(np.float32)
        out = a * (1.0 - e) + b * e
        return np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def zoom_in(frame_a: np.ndarray, frame_b: np.ndarray, t: float) -> np.ndarray:
        e = ease_in_out(t)
        h, w = frame_b.shape[:2]

        # frame_b punches in from 88% -> 100% scale while cross-fading over frame_a
        scale = 0.88 + 0.12 * e
        img_b = _to_pil(frame_b)
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        scaled = img_b.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        canvas.paste(scaled, ((w - new_w) // 2, (h - new_h) // 2))
        b_arr = _to_np(canvas).astype(np.float32)
        a_arr = frame_a.astype(np.float32)
        out = a_arr * (1.0 - e) + b_arr * e
        return np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def light_sweep(frame_a: np.ndarray, frame_b: np.ndarray, t: float) -> np.ndarray:
        e = ease_in_out(t)
        h, w = frame_a.shape[:2]

        base = frame_a.astype(np.float32) * (1.0 - e) + frame_b.astype(np.float32) * e

        # Diagonal bright band sweeping left -> right, width ~18% of frame
        band_centre = -0.2 * w + e * 1.4 * w
        band_width = 0.16 * w
        xs = np.arange(w, dtype=np.float32)
        dist = np.abs(xs - band_centre)
        band = np.clip(1.0 - dist / band_width, 0.0, 1.0) ** 2
        band_row = band[np.newaxis, :, np.newaxis]  # (1, w, 1)
        highlight = band_row * 90.0  # additive brightness

        out = base + highlight
        return np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def digital_push(frame_a: np.ndarray, frame_b: np.ndarray, t: float, direction: str = "left") -> np.ndarray:
        e = ease_in_out(t)
        h, w = frame_a.shape[:2]
        out = np.zeros_like(frame_a)

        if direction in ("left", "right"):
            offset = int(w * e)
            if direction == "left":
                out[:, : w - offset] = frame_a[:, offset:]
                if offset > 0:
                    out[:, w - offset:] = frame_b[:, :offset]
            else:  # right
                out[:, offset:] = frame_a[:, : w - offset]
                if offset > 0:
                    out[:, :offset] = frame_b[:, w - offset:]
        else:  # up/down
            offset = int(h * e)
            if direction == "up":
                out[: h - offset, :] = frame_a[offset:, :]
                if offset > 0:
                    out[h - offset:, :] = frame_b[:offset, :]
            else:  # down
                out[offset:, :] = frame_a[: h - offset, :]
                if offset > 0:
                    out[:offset, :] = frame_b[h - offset:, :]

        return out.astype(np.uint8)

    @staticmethod
    def blur_fade(frame_a: np.ndarray, frame_b: np.ndarray, t: float) -> np.ndarray:
        e = ease_in_out(t)
        # frame_a blurs increasingly as it fades out; frame_b sharpens in
        blur_radius = 14 * e
        img_a = _to_pil(frame_a)
        if blur_radius > 0.3:
            img_a = img_a.filter(ImageFilter.GaussianBlur(blur_radius))
        a_arr = _to_np(img_a).astype(np.float32)
        b_arr = frame_b.astype(np.float32)
        out = a_arr * (1.0 - e) + b_arr * e
        return np.clip(out, 0, 255).astype(np.uint8)

    # ── Dispatch by TransitionType value ────────────────────
    @classmethod
    def apply(cls, kind: str, frame_a: np.ndarray, frame_b: np.ndarray, t: float, direction: str = "left") -> np.ndarray:
        if kind == "zoom":
            return cls.zoom_in(frame_a, frame_b, t)
        if kind == "sweep":
            return cls.light_sweep(frame_a, frame_b, t)
        if kind == "push":
            return cls.digital_push(frame_a, frame_b, t, direction)
        if kind == "blur":
            return cls.blur_fade(frame_a, frame_b, t)
        return cls.cross_dissolve(frame_a, frame_b, t)
