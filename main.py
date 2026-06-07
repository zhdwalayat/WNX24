# main.py
import sys
import os
from loguru import logger
from utils.helpers import ensure_dirs

# ── Configure logging ──────────────────────────────────────
logger.add(
    os.path.join("output", "logs", "meridian_{time:YYYY-MM-DD}.log"),
    rotation = "1 day",
    retention = "7 days",
    level    = "INFO",
    format   = "{time:HH:mm:ss} | {level} | {message}",
)

def main():
    ensure_dirs()

    logger.info("")
    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║   MNN — MERIDIAN NEWS NETWORK — STARTING   ║")
    logger.info("║      'Where the world meets.'            ║")
    logger.info("╚══════════════════════════════════════════╝")
    logger.info("")

    from orchestrator import start_24_7, run_now

    if len(sys.argv) > 1:
        show_id = sys.argv[1]
        logger.info(f"Manual run — show: {show_id}")
        run_now(show_id)
    else:
        start_24_7()

if __name__ == "__main__":
    main()
