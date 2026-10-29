# agents/image_agent.py
"""Creative-Commons image sourcing agent — replaces the HeyGen avatar
video frame with real editorial imagery for each story.

Sources are tried in priority order and are all free / CC-licensed:
  1. Wikimedia Commons  — no key required
  2. NASA Image Library — no key required, public domain
  3. Unsplash           — free tier, requires UNSPLASH_ACCESS_KEY
  4. Pexels             — free tier, requires PEXELS_API_KEY
  5. Solid-colour card   — always-available fallback, no network needed

Never queries Getty Images or Shutterstock. Every fetched image's
source URL is logged for attribution.
"""
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path

import requests
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from config.settings import config
from core.models import VerifiedStory

# ── Output frame size ───────────────────────────────────────
FRAME_W, FRAME_H = 1920, 1080

ROOT_DIR   = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
FONT_BOLD  = str(ASSETS_DIR / "fonts" / "DejaVuSansMono-Bold.ttf")

REQUEST_TIMEOUT = 15
USER_AGENT      = "WNX24-NewsBot/1.0 (https://wnx24.example; editorial image sourcing)"

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "as", "is", "are", "was", "were", "be", "been", "by",
    "from", "that", "this", "it", "its", "his", "her", "their", "will",
    "has", "have", "had", "after", "over", "into", "amid", "amid", "than",
    "says", "said", "new", "up", "out", "about", "not", "no", "may",
    "can", "could", "would", "should", "who", "what", "when", "where",
    "how", "why", "which", "how", "more", "most", "some", "all", "one",
}

# ── Fallback card colour palette (cycled per story) ────────
_FALLBACK_COLOURS = [
    (10, 12, 40), (24, 32, 64), (40, 16, 20), (12, 40, 32), (30, 20, 50),
]


# ── 1. Keyword extraction ──────────────────────────────────
def extract_keywords(story: VerifiedStory, max_keywords: int = 5) -> list[str]:
    raw = getattr(story, "raw", story)

    keywords: list[str] = []

    # Prefer keywords already matched against config.content.keywords —
    # these are curated, high-signal search terms (e.g. "Pakistan", "AI").
    for kw in getattr(raw, "keywords", []) or []:
        kw = kw.strip()
        if kw and kw.lower() not in (k.lower() for k in keywords):
            keywords.append(kw)

    if len(keywords) < max_keywords:
        title = getattr(raw, "title", "") or ""
        words = re.findall(r"[A-Za-z][A-Za-z\-']+", title)
        for w in words:
            wl = w.lower()
            if wl in _STOPWORDS or len(w) < 3:
                continue
            if wl not in (k.lower() for k in keywords):
                keywords.append(w)
            if len(keywords) >= max_keywords:
                break

    return keywords[:max_keywords] or ["world news"]


# ── 2. Wikimedia Commons ───────────────────────────────────
def fetch_wikimedia_images(keywords: list[str], count: int = 3) -> list[str]:
    query = " ".join(keywords)
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action":        "query",
                "format":        "json",
                "generator":     "search",
                "gsrsearch":     query,
                "gsrnamespace":  6,          # File: namespace
                "gsrlimit":      count * 4,
                "prop":          "imageinfo",
                "iiprop":        "url|size|extmetadata",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        pages = (resp.json().get("query", {}) or {}).get("pages", {}) or {}

        urls: list[str] = []
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            url = info.get("url", "")
            if not re.search(r"\.(jpe?g|png)$", url, re.IGNORECASE):
                continue
            if info.get("width", 0) < 800:
                continue
            urls.append(url)
            logger.info(f"Wikimedia image found — {url}")
            if len(urls) >= count:
                break
        return urls
    except Exception as e:
        logger.warning(f"Wikimedia Commons search failed: {e}")
        return []


# ── 3. NASA Image Library ──────────────────────────────────
def fetch_nasa_images(keywords: list[str], count: int = 3) -> list[str]:
    query = " ".join(keywords)
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            params={"q": query, "media_type": "image"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        items = (resp.json().get("collection", {}) or {}).get("items", []) or []

        urls: list[str] = []
        for item in items:
            links = item.get("links") or []
            for link in links:
                href = link.get("href", "")
                if re.search(r"\.(jpe?g|png)$", href, re.IGNORECASE):
                    urls.append(href)
                    logger.info(f"NASA image found (public domain) — {href}")
                    break
            if len(urls) >= count:
                break
        return urls
    except Exception as e:
        logger.warning(f"NASA image search failed: {e}")
        return []


# ── 4. Unsplash ────────────────────────────────────────────
def fetch_unsplash_images(keywords: list[str], count: int = 3) -> list[str]:
    access_key = config.api.unsplash_access_key
    if not access_key:
        return []

    query = " ".join(keywords)
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": count},
            headers={
                "Authorization": f"Client-ID {access_key}",
                "User-Agent":    USER_AGENT,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", []) or []

        urls: list[str] = []
        for r in results:
            url = (r.get("urls") or {}).get("regular", "")
            if url:
                urls.append(url)
                logger.info(f"Unsplash image found — {url} (by {(r.get('user') or {}).get('name', 'unknown')})")
            if len(urls) >= count:
                break
        return urls
    except Exception as e:
        logger.warning(f"Unsplash search failed: {e}")
        return []


# ── 5. Pexels ───────────────────────────────────────────────
def fetch_pexels_images(keywords: list[str], count: int = 3) -> list[str]:
    api_key = config.api.pexels_api_key
    if not api_key:
        return []

    query = " ".join(keywords)
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": count},
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", []) or []

        urls: list[str] = []
        for p in photos:
            url = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large", "")
            if url:
                urls.append(url)
                logger.info(f"Pexels image found — {url} (by {p.get('photographer', 'unknown')})")
            if len(urls) >= count:
                break
        return urls
    except Exception as e:
        logger.warning(f"Pexels search failed: {e}")
        return []


# ── Download + normalise ───────────────────────────────────
def download_image(url: str, save_path: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(resp.content)

        img = Image.open(save_path).convert("RGB")
        img = _resize_cover(img, FRAME_W, FRAME_H)
        img.save(save_path, "JPEG", quality=92)
        return save_path
    except Exception as e:
        logger.warning(f"Failed to download image {url}: {e}")
        try:
            os.remove(save_path)
        except OSError:
            pass
        return None


def _resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize + centre-crop `img` to exactly (target_w, target_h)
    without distorting the aspect ratio (like CSS `object-fit: cover`)."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


# ── Fallback: solid-colour card with headline ──────────────
def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD, size)
    except Exception:
        return ImageFont.load_default()


def make_fallback_image(headline: str, save_path: str, seed: int = 0) -> str:
    colour = _FALLBACK_COLOURS[seed % len(_FALLBACK_COLOURS)]
    img  = Image.new("RGB", (FRAME_W, FRAME_H), colour)
    draw = ImageDraw.Draw(img)

    # subtle diagonal accent
    draw.rectangle([0, FRAME_H - 12, FRAME_W, FRAME_H], fill=(230, 57, 70))

    font  = _font(72)
    lines = textwrap.wrap((headline or "WNX24 News").strip(), width=28)[:5]
    line_h  = 92
    total_h = len(lines) * line_h
    y = (FRAME_H - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (FRAME_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, fill=(240, 240, 240), font=font)
        y += line_h

    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    img.save(save_path, "JPEG", quality=92)
    logger.info(f"Fallback image card generated — {save_path}")
    return save_path


# ── 6. Aggregate across all sources ────────────────────────
def get_story_images(story: VerifiedStory, out_dir: str, count: int = 5) -> list[str]:
    raw      = getattr(story, "raw", story)
    keywords = extract_keywords(story, max_keywords=5)
    logger.info(f"Image search keywords: {keywords}")

    fetchers = [
        ("wikimedia", fetch_wikimedia_images),
        ("nasa",      fetch_nasa_images),
        ("unsplash",  fetch_unsplash_images),
        ("pexels",    fetch_pexels_images),
    ]

    local_paths: list[str] = []
    idx = 0
    for source_name, fetch_fn in fetchers:
        if len(local_paths) >= count:
            break
        remaining = count - len(local_paths)
        try:
            urls = fetch_fn(keywords, count=remaining)
        except Exception as e:
            logger.warning(f"{source_name} fetch raised: {e}")
            urls = []

        for url in urls:
            if len(local_paths) >= count:
                break
            ext = "jpg" if not url.lower().endswith(".png") else "png"
            save_path = os.path.join(out_dir, f"{source_name}_{idx}.{ext}")
            result = download_image(url, save_path)
            if result:
                local_paths.append(result)
                logger.info(f"Image {idx + 1}/{count} sourced from {source_name} — {url}")
                idx += 1

    # Always guarantee a minimum of 3 usable images
    min_needed = max(3, count) if count >= 3 else 3
    while len(local_paths) < min(min_needed, count) or len(local_paths) < 3:
        headline  = getattr(raw, "title", "WNX24 News")
        save_path = os.path.join(out_dir, f"fallback_{idx}.jpg")
        local_paths.append(make_fallback_image(headline, save_path, seed=idx))
        idx += 1
        if len(local_paths) >= max(count, 3):
            break

    return local_paths[:max(count, 3)] if count < 3 else local_paths[:count]


# ── Main entry point ────────────────────────────────────────
def run_image_agent(story: VerifiedStory, show_id: str, count: int = 5) -> list[str]:
    logger.info("Starting Image Agent — WNX24 (Creative Commons sourcing)")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir   = os.path.join(config.output_dir, "images", f"{show_id}_{timestamp}")
    os.makedirs(out_dir, exist_ok=True)

    images = get_story_images(story, out_dir, count=count)

    logger.info(f"Image Agent complete — {len(images)} images in {out_dir}")
    return images
