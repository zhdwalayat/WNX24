"""broadcast_os/agents/asset_discovery.py — Asset Discovery Agent.

Wraps the existing agents/image_agent.py fetchers (Wikimedia Commons, NASA
Image Library, Unsplash, Pexels, generated fallback card) instead of
duplicating their HTTP/licensing logic, and adds the AssetResult metadata
(licence, confidence, source attribution) the BroadcastOS render pipeline
needs. Never queries Getty/Shutterstock/AFP — same guarantee as
agents/image_agent.py.

UN Photo Library (listed in the original spec) is intentionally not wired
in: UN Photo has no stable, keyless public search API comparable to
Wikimedia/NASA's — same "documented, not faked" approach this codebase
already takes with agents/translation_agent.py and
agents/social_media_agent.py. Priority order falls straight from
NASA to Unsplash.
"""
from __future__ import annotations

import os
from datetime import datetime

from loguru import logger
from PIL import Image

from agents.image_agent import (
    download_image,
    fetch_nasa_images,
    fetch_pexels_images,
    fetch_unsplash_images,
    fetch_wikimedia_images,
    make_fallback_image,
)
from broadcast_os.interfaces import AssetResult, StoryBlueprint
from config.settings import config

_LICENCES = {
    "wikimedia": "CC0 / CC-BY-SA (Wikimedia Commons)",
    "nasa": "Public Domain (NASA Image Library)",
    "unsplash": "Unsplash License (free to use)",
    "pexels": "Pexels License (free to use)",
    "fallback": "Generated (WNX24 editorial card)",
}
_CONFIDENCE = {
    "wikimedia": 0.9,
    "nasa": 0.9,
    "unsplash": 0.7,
    "pexels": 0.7,
    "fallback": 0.3,
}
_FETCHERS = [
    ("wikimedia", fetch_wikimedia_images),
    ("nasa", fetch_nasa_images),
    ("unsplash", fetch_unsplash_images),
    ("pexels", fetch_pexels_images),
]


def _keywords_from_blueprint(blueprint: StoryBlueprint, max_keywords: int = 5) -> list[str]:
    words: list[str] = []
    for w in [blueprint.location, blueprint.category, *blueprint.title.split()]:
        w = w.strip()
        if len(w) < 3:
            continue
        if w.lower() not in (k.lower() for k in words):
            words.append(w)
        if len(words) >= max_keywords:
            break
    return words or ["world news"]


def _image_size(path: str) -> tuple[int, int]:
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return (0, 0)


class AssetDiscoveryAgent:
    """
    Priority order:
    1. Wikimedia Commons API (CC licensed)
    2. NASA Image Library (public domain)
    3. Unsplash API (free tier — UNSPLASH_ACCESS_KEY)
    4. Pexels API (free tier — PEXELS_API_KEY)
    5. Generate placeholder with PIL

    Minimum 3 images always returned. Cached in output/assets/{story_id}/.
    """

    def discover(self, blueprint: StoryBlueprint, count: int = 5) -> list[AssetResult]:
        story_id = _safe_id(blueprint)
        keywords = _keywords_from_blueprint(blueprint)
        logger.info(f"Asset Discovery — keywords: {keywords}")
        return self.discover_for_keywords(
            keywords, story_id, count=count, fallback_caption=blueprint.headline or blueprint.title,
        )

    def discover_for_keywords(
        self, keywords: list[str], out_id: str, count: int = 5, fallback_caption: str = "",
    ) -> list[AssetResult]:
        """Same fetch/fallback pipeline as discover(), but keyed off plain
        keywords instead of a full StoryBlueprint — used to fetch imagery
        per-story for a multi-story bulletin's footage scene, where each
        story never gets its own StoryBlueprint (only the lead story does)."""
        out_dir = os.path.join(config.output_dir, "assets", out_id)
        os.makedirs(out_dir, exist_ok=True)

        results: list[AssetResult] = []
        idx = 0

        for source_name, fetch_fn in _FETCHERS:
            if len(results) >= count:
                break
            remaining = count - len(results)
            try:
                urls = fetch_fn(keywords, count=remaining)
            except Exception as e:
                logger.warning(f"Asset Discovery: {source_name} fetch raised: {e}")
                urls = []

            for url in urls:
                if len(results) >= count:
                    break
                ext = "png" if url.lower().endswith(".png") else "jpg"
                save_path = os.path.join(out_dir, f"{source_name}_{idx}.{ext}")
                local_path = download_image(url, save_path)
                if not local_path:
                    continue
                w, h = _image_size(local_path)
                results.append(AssetResult(
                    local_path=local_path,
                    source_url=url,
                    source_name=source_name,
                    licence=_LICENCES[source_name],
                    confidence=_CONFIDENCE[source_name],
                    width=w,
                    height=h,
                ))
                logger.info(
                    f"Asset {len(results)}/{count} — {source_name} "
                    f"({_LICENCES[source_name]}) — {url}"
                )
                idx += 1

        # Guarantee a minimum of 3 usable assets via generated fallback cards
        caption = fallback_caption or (keywords[0] if keywords else "WNX24")
        while len(results) < max(count, 3):
            save_path = os.path.join(out_dir, f"fallback_{idx}.jpg")
            make_fallback_image(caption, save_path, seed=idx)
            w, h = _image_size(save_path)
            results.append(AssetResult(
                local_path=save_path,
                source_url="",
                source_name="fallback",
                licence=_LICENCES["fallback"],
                confidence=_CONFIDENCE["fallback"],
                width=w,
                height=h,
            ))
            idx += 1

        logger.info(f"Asset Discovery complete — {len(results)} assets in {out_dir}")
        return results[:max(count, 3)]


def _safe_id(blueprint: StoryBlueprint) -> str:
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", blueprint.title.lower()).strip("_")[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{slug or 'story'}_{ts}"


def run_asset_discovery(blueprint: StoryBlueprint, count: int = 5) -> list[AssetResult]:
    return AssetDiscoveryAgent().discover(blueprint, count=count)
