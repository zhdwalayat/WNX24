# main.py
import os
import sys

from loguru import logger

from utils.helpers import ensure_dirs

# ── Configure logging ──────────────────────────────────────
logger.add(
    os.path.join("output", "logs", "wnx24_{time:YYYY-MM-DD}.log"),
    rotation = "1 day",
    retention = "7 days",
    level    = "INFO",
    format   = "{time:HH:mm:ss} | {level} | {message}",
)

def main():
    ensure_dirs()

    logger.info("")
    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║             WNX24 — STARTING             ║")
    logger.info("║          'World News. Always.'           ║")
    logger.info("╚══════════════════════════════════════════╝")
    logger.info("")

    from orchestrator import run_now, start_24_7

    if len(sys.argv) > 1:
        show_id = sys.argv[1]
        logger.info(f"Manual run — show: {show_id}")
        run_now(show_id)
    else:
        start_24_7()

if __name__ == "__main__":
    main()
