# video/heygen_client.py
import os
import time
import httpx
from loguru import logger
from config.settings import config

HEYGEN_BASE = "https://api.heygen.com"

class HeyGenClient:
    def __init__(self):
        self.api_key = config.api.heygen_api_key
        self.avatar_id = os.getenv("HEYGEN_AVATAR_ID", "Aria_public_3_20240108")
        self.voice_id  = os.getenv("HEYGEN_VOICE_ID",  "en-US-AriaNeural")

    def _headers(self):
        return {
            "X-Api-Key":    self.api_key,
            "Content-Type": "application/json",
        }

    # ── Submit video job ───────────────────────────────────
    def submit(self, script_text: str, duration_secs: int = 60) -> str:
        if not self.api_key:
            logger.warning("No HeyGen key — returning mock job ID")
            return f"mock_{hash(script_text) & 0xFFFFFF:06x}"

        payload = {
            "video_inputs": [{
                "character": {
                    "type":      "avatar",
                    "avatar_id": self.avatar_id,
                    "style":     "normal",
                },
                "voice": {
                    "type":       "text",
                    "input_text": script_text,
                    "voice_id":   self.voice_id,
                    "speed":      1.0,
                },
                "background": {
                    "type":  "color",
                    "value": "#1a1a2e",
                },
            }],
            "dimension": { "width": 1920, "height": 1080 },
            "caption": True,
        }

        try:
            resp = httpx.post(
                f"{HEYGEN_BASE}/v2/video/generate",
                headers = self._headers(),
                json    = payload,
                timeout = 30,
            )
            resp.raise_for_status()
            job_id = resp.json()["data"]["video_id"]
            logger.info(f"HeyGen job submitted — {job_id}")
            return job_id
        except Exception as e:
            logger.error(f"HeyGen submit failed: {e}")
            return ""

    # ── Poll until complete ────────────────────────────────
    def poll(self, job_id: str, timeout: int = 600) -> str:
        if job_id.startswith("mock_"):
            logger.info("Mock mode — skipping poll")
            return ""

        elapsed = 0
        while elapsed < timeout:
            try:
                resp = httpx.get(
                    f"{HEYGEN_BASE}/v1/video_status.get",
                    headers = self._headers(),
                    params  = {"video_id": job_id},
                    timeout = 15,
                )
                resp.raise_for_status()
                data   = resp.json()["data"]
                status = data["status"]

                if status == "completed":
                    url = data["video_url"]
                    logger.info(f"HeyGen complete — {url}")
                    return url
                elif status == "failed":
                    logger.error(f"HeyGen failed: {data.get('error')}")
                    return ""
                else:
                    logger.info(f"HeyGen status: {status} ({elapsed}s)")

            except Exception as e:
                logger.warning(f"Poll error: {e}")

            time.sleep(15)
            elapsed += 15

        logger.error(f"HeyGen timeout after {timeout}s")
        return ""

    # ── Download MP4 ───────────────────────────────────────
    def download(self, url: str, job_id: str) -> str:
        out_dir = os.path.join(config.output_dir, "videos")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{job_id}.mp4")

        try:
            with httpx.stream("GET", url, timeout=120) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes(8192):
                        f.write(chunk)
            logger.info(f"Video saved — {path}")
            return path
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return ""

    # ── Full pipeline ──────────────────────────────────────
    def generate(self, script_text: str, duration_secs: int = 60) -> tuple:
        job_id = self.submit(script_text, duration_secs)
        if not job_id:
            return "", ""
        if job_id.startswith("mock_"):
            return job_id, ""
        video_url  = self.poll(job_id)
        local_path = self.download(video_url, job_id) if video_url else ""
        return job_id, local_path

# Singleton
heygen = HeyGenClient()
