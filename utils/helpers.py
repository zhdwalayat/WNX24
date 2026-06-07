# utils/helpers.py
import os
import json
import hashlib
from datetime import datetime
from typing import Any
from loguru import logger
from config.settings import config

def ensure_dirs():
    for d in ["videos", "thumbnails", "metadata", "logs"]:
        os.makedirs(os.path.join(config.output_dir, d), exist_ok=True)
    logger.info("Output directories ready")

def save_json(data: Any, filename: str) -> str:
    out_dir = os.path.join(config.output_dir, "metadata")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    return path

def load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load JSON: {e}")
        return None

def truncate(text: str, max_chars: int = 100) -> str:
    return text[:max_chars] + "..." if len(text) > max_chars else text

def word_count(text: str) -> int:
    return len(text.split())

def estimate_duration(text: str, wpm: int = 150) -> int:
    return int((word_count(text) / wpm) * 60)

def timestamp_pkt() -> str:
    return datetime.now().strftime("%d %b %Y  |  %H:%M PKT")

def make_safe_filename(text: str, max_len: int = 50) -> str:
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in text)
    return safe[:max_len].strip().replace(" ", "_")
