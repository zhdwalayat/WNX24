# WNX24 — "World News. Always."

WNX24 is a fully autonomous, AI-powered news broadcast pipeline. It scrapes
breaking news from ~60 RSS/news sources, verifies and scores stories for
credibility, writes broadcast-style scripts with an LLM, generates narrated
video with an AI anchor, builds a thumbnail, and uploads the finished video
to YouTube — on a 24/7 schedule across 17 different shows.

## How it works

Every broadcast runs the same six-step pipeline (`orchestrator.run_broadcast`):

1. **Source stories** — pull verified, unbroadcast stories from the local
   SQLite database (`output/wnx24_news.db`). If the database is empty or has
   nothing fresh for the show, it falls back to scraping live.
2. **Write the script** — `agents/script_writer_agent.py` sends a
   show-specific prompt to Groq (`llama-3.3-70b-versatile`) and parses the
   response into hook / what-happened / why-it-matters / expert-insight /
   sign-off sections, following BBC/Reuters/AP-style newsroom rules
   (active voice, no banned filler words, British spelling, PEMRA-safe
   language for Pakistan-focused shows).
3. **Generate the avatar video** — `agents/avatar_agent.py` converts each
   script section to speech with `edge-tts` (free Microsoft neural voices)
   and hands the audio off to `video/video_editor.py`, which renders
   Pillow-based broadcast frames and assembles them with `ffmpeg` into the
   final MP4 (intro card, per-section scenes, scrolling ticker, outro card).
   A paid HeyGen avatar path also exists (`video/heygen_client.py`) but is
   currently disabled — see [Known issues](#known-issues).
4. **Generate the thumbnail** — `agents/thumbnail_agent.py` renders a
   1280×720 YouTube thumbnail with a show-specific accent colour, badge, and
   the WNX24 logo watermark.
5. **Upload** — `agents/uploader_agent.py` uploads the video to YouTube via
   the Data API v3 (OAuth2, auto-refreshing token), sets the thumbnail, and
   writes SEO metadata (title, chapters, description, tags). If YouTube
   credentials are missing, the daily upload cap is reached, or the API
   call fails, it falls back to a **mock upload** that just saves the
   metadata JSON locally.
6. **Record the broadcast** — the story is marked `broadcast` in the
   database and a row is written to the `broadcasts` table
   (`core/database.py`), including the YouTube URL, script, word count, and
   thumbnail path.

### Story scraping & verification

- `agents/trend_detection_agent.py` scrapes Google News, Hacker News, Reddit
  (public `.rss` feeds, no OAuth needed), and ~60 mainstream/regional RSS
  feeds (World, Economy, Conflict, Science, Health, Tech, Pakistan, Asia,
  Middle East, American, Chinese outlets), dedupes by URL hash, and scores
  each story.
- `agents/verification_agent.py` computes a confidence score per story using
  cross-source confirmation, source-tier trust (Tier 1/2/3, with RT/Sputnik/
  PressTV hard-blocked), keyword relevance, PR-language penalties, and a
  Pakistan-specific cross-check (requires a second Pakistani outlet to
  confirm before airing). Only stories that clear `min_confidence_score`
  (default `0.75`) are marked `verified` and eligible for broadcast.

### Shows

17 shows are defined in `config/settings.py` (`SchedulerConfig`), each with
its own anchor opening line, schedule (PKT / Asia-Karachi), video length,
story count, and minimum score threshold:

| Show | Schedule (PKT) | Length | Focus |
|---|---|---|---|
| `breaking` | every 30 min | 60s | Breaking news (≤30 min old) |
| `morning` | 06:00 | 5 min | Morning briefing |
| `explainer` | 08:00 | 2.5 min | Single-story explainer |
| `pakistan` | 08:00 / 14:00 / 20:00 | 3 min | Pakistan Report |
| `world_affairs` | 07:00 | 3 min | World Affairs |
| `asia` | 09:00 | 3 min | Asia Tech Report |
| `economy` | 10:00 | 3 min | Markets & Economy |
| `midday` | 11:00 | 3 min | Midday Bulletin |
| `deep_dive` | 13:00 | 5 min | Deep Dive (single story) |
| `science` | 14:00 | 3 min | Science & Climate Watch |
| `mideast` | 15:00 | 3 min | Middle East & South Asia |
| `health` | 16:00 | 3 min | Health & Medicine Report |
| `evening` | 17:00 | 3 min | Evening Bulletin |
| `conflict` | 20:00 (Mon–Sat) | 3 min | War & Conflict Report |
| `primetime` | 21:00 | 7 min | Prime Time Special |
| `summary` | 23:00 | 10 min | Daily Summary (10 stories) |
| `week_review` | Sunday 20:00 | 10 min | Week in Review (5 stories) |

The scheduler (`orchestrator.setup_scheduler`) also runs a breaking-news
check every 5 minutes, a daily YouTube cleanup job at 03:00, and a monthly
database cleanup.

## Project layout

```
agents/        Scraping, verification, scripting, avatar, thumbnail, upload
config/        Settings (dataclasses) + YouTube OAuth client/token
core/          Pydantic models + SQLite access layer
dashboard/     FastAPI "Control Room" web UI
orchestrator.py  Pipeline + APScheduler setup, entry points
scraper/       Standalone fast-cycle news watcher
utils/         Shared helpers (dirs, JSON, word counts, PKT timestamps)
video/         ffmpeg/Pillow video assembly + HeyGen client
main.py        CLI entry point
```

## Setup

Requires Python **3.14+** and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

This installs all dependencies into `.venv/`, including `edge-tts` for free
neural text-to-speech and `imageio-ffmpeg` (bundles its own ffmpeg binary —
no separate ffmpeg install needed).

### Configuration

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Used for |
|---|---|---|
| `GROQ_API_KEY` | **Yes** | Script writing (Groq, free tier) |
| `YOUTUBE_API_KEY` | For real uploads | YouTube Data API v3 |
| `YOUTUBE_CHANNEL_ID` | For real uploads | YouTube cleanup job |
| `YOUTUBE_MAX_DAILY_UPLOADS` | No (default `10`) | Daily upload cap before falling back to mock |
| `EDGE_TTS_VOICE` | No (default `en-US-AriaNeural`) | Narration voice |
| `HEYGEN_API_KEY` / `HEYGEN_AVATAR_ID` / `HEYGEN_VOICE_ID` | No | Paid avatar video (currently disabled) |
| `OPENAI_API_KEY` | No | Reserved, not currently used |
| `NEWSAPI_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | No | Reserved, not currently used (Reddit is scraped via public RSS) |

**YouTube OAuth**: download an OAuth client secret from Google Cloud Console
and save it as `config/youtube_oauth.json`. The first real upload opens a
browser for authorisation and saves a refreshable token to
`config/youtube_token.json`.

## Running

### Run one show immediately

```bash
.venv/Scripts/python.exe main.py breaking
```

Replace `breaking` with any show ID from the table above. Output (video,
thumbnail, logs, metadata) is written under `output/`.

### Run the 24/7 scheduler

```bash
.venv/Scripts/python.exe main.py
```

Starts `orchestrator.start_24_7()`, which runs every show on its configured
cron schedule (PKT) plus the breaking-news, cleanup, and review jobs,
indefinitely.

### Dashboard ("Control Room")

```bash
uvicorn dashboard.app:app --reload --port 8000
```

A dark-themed web UI to trigger shows manually, watch the pipeline log live,
browse generated videos/thumbnails, and check API/database health at
`/health`.

### Standalone news watcher

```bash
.venv/Scripts/python.exe scraper/news_watcher.py
```

Runs only the scrape → verify → save loop (breaking sources every 5 minutes,
full scrape every 15 minutes) without writing/uploading videos — useful for
pre-warming the database.

## Known issues

- **Groq daily token quota**: the free tier for `llama-3.3-70b-versatile` is
  100,000 tokens/day. High-word-count shows (`summary`, `week_review`) can
  exhaust it, which previously caused those broadcasts to fail outright.
  `_call_groq_with_retry` now detects a "tokens per day" rate-limit error and
  falls back to `llama-3.1-8b-instant` (a separate, higher-quota free model)
  instead of retrying the same exhausted model.
- **Some RSS feeds fail intermittently** (DNS/connection errors) — this is
  non-fatal and logged as a warning per source. `feeds.apnews.com` and
  `feeds.reuters.com` (used by the "AP News", "Reuters World", and "Reuters
  Health" sources) appear to be permanently discontinued and consistently
  fail; other feeds (Washington Post, Samaa News, The Hindu, etc.) fail only
  occasionally due to network flakiness.
- **HeyGen avatar is configured but disabled**: `HEYGEN_API_KEY`,
  `HEYGEN_AVATAR_ID`, and `HEYGEN_VOICE_ID` can be set, but
  `agents/avatar_agent.py` hardcodes `USE_HEYGEN = False`, so every broadcast
  uses the free `edge-tts` + ffmpeg path. Set `USE_HEYGEN = True` to enable
  the paid HeyGen avatar (with automatic fallback to the local path on
  failure).
- **YouTube upload limits**: real uploads are capped at
  `YOUTUBE_MAX_DAILY_UPLOADS` (default 10) per rolling 24h, based on
  observed `uploadLimitExceeded` errors on new channels. Once capped, uploads
  fall back to mock mode (metadata saved to `output/metadata/`, no actual
  video uploaded).


