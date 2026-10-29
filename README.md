# WNX24 — "World News. Always."

WNX24 ("World News. Always.") is a fully autonomous, AI-powered news broadcast
pipeline. It collects breaking stories from about 60 RSS and news sources,
scores them for credibility, and cross-verifies them across sources. It gathers
supporting evidence, writes broadcast-style scripts with an LLM, and runs each
script through editorial and mandatory legal/ethics review. It then produces
narrated 1080p video with an AI anchor, lower thirds and a headline ticker,
builds a thumbnail, and uploads the result to YouTube. A scheduler runs 17
distinct shows around the clock, and a FastAPI health dashboard monitors it.

## How it works

Every broadcast follows a collect-and-publish flow (`orchestrator.run_broadcast`):

```
Information Received (scrapers) -> Source Identification / Credibility Check
  (verification_agent tiers + scoring) -> Cross Verification
  (verification_agent cross-source confirmation) -> Evidence Collection
  (research_agent) -> [write the script] -> Editorial Review (style_agent)
  -> Legal / Ethics Review (compliance_agent, mandatory) -> Publish (uploader_agent)
```

1. **Source stories** — pull verified, unbroadcast stories from the local
   SQLite database (`output/wnx24_news.db`). If the database is empty or has
   nothing fresh for the show, it falls back to scraping live. For any show
   with more fresh candidates than it needs (everything except `breaking`),
   `agents/planning_agent.py` picks and orders the final line-up from that
   pool instead of a blind top-N-by-score slice — falls back to the score
   order itself if its LLM call fails.
2. **Evidence Collection** — before writing, `agents/research_agent.py`
   gathers a short web-research brief on the lead story (via
   `agents/web_search_agent.py`) for *every* show, not just the analysis/
   explainer formats. Each prompt treats it strictly as background context,
   never a mandate to include it — `breaking`'s "zero analysis" rule and
   the bulletin formats' brevity still govern the actual output. A failed
   research call never blocks script generation.
3. **Write the script** — `agents/script_writer_agent.py` sends a
   show-specific prompt (now including the research brief) to Groq
   (`llama-3.3-70b-versatile`) and parses the response into hook /
   what-happened / why-it-matters / expert-insight / sign-off sections,
   following BBC/Reuters/AP-style newsroom rules and the nine-principle
   journalism charter in `agents/editorial_standards.py` (PEMRA-safe
   language for Pakistan-focused shows included).
4. **Editorial Review** — `agents/style_agent.py` lints the finished script
   against WNX24's house style guide (banned filler words, British vs.
   American spelling, sentence length) — informational only, logged but
   never blocking.
5. **Legal / Ethics Review** — `agents/compliance_agent.py` then reviews the
   script against the same journalism charter for defamation risk, unproven
   claims stated as fact, promotional/commercial bias, and PEMRA-restricted
   content. This is a **mandatory hard gate on every broadcast** — a failed
   review aborts it entirely. There's no human editor in the loop to decide
   "if needed," so it always runs, never conditionally.
6. **Compose the broadcast video** — `video/video_composer.py` is the
   primary pipeline: `agents/voice_agent.py` narrates each script section
   with `edge-tts` (free Microsoft neural voices, falling back to `espeak`
   offline), `agents/image_agent.py` sources Creative-Commons editorial
   imagery for the story (Wikimedia Commons → NASA → Unsplash → Pexels →
   a generated solid-colour headline card, in that order), and the composer
   applies a Ken Burns pan/zoom to each image, composites it onto the WNX24
   newsroom background (`assets/newsroom_bg.jpg`), burns in a lower third
   and scrolling ticker, and stitches in the intro/outro. If this pipeline
   fails, `orchestrator.py` falls back to `agents/avatar_agent.py` (the
   older `edge-tts` + `video/video_editor.py` Pillow-frame pipeline). A paid
   HeyGen avatar path also exists (`video/heygen_client.py`) but is disabled
   by default — see [Known issues](#known-issues).
7. **Generate the thumbnail** — `agents/thumbnail_agent.py` renders a
   1280×720 YouTube thumbnail with a show-specific accent colour, badge, and
   the WNX24 logo watermark. The on-thumbnail headline comes from
   `agents/headline_agent.py` (an LLM rewrite of the raw scraped title),
   falling back to the title itself if that call fails.
8. **Publish** — `agents/uploader_agent.py` uploads the video to YouTube via
   the Data API v3 (OAuth2, auto-refreshing token), sets the thumbnail, and
   writes SEO metadata (title, chapters, description, tags) via
   `agents/seo_agent.py`, which reuses the same headline the thumbnail
   already used (`package.thumbnail.headline`) so the title and thumbnail
   never disagree, rather than spending a second LLM call on it. If YouTube
   credentials are missing, the daily upload cap is reached, or the API
   call fails, it falls back to a **mock upload** that just saves the
   metadata JSON locally.
9. **Record the broadcast** — the story is marked `broadcast` in the
   database and a row is written to the `broadcasts` table
   (`core/database.py`), including the YouTube URL, script, word count, and
   thumbnail path.
10. **Track performance** — separately from the per-broadcast pipeline,
    `agents/performance_agent.py` runs once daily and pulls view/like/comment
    counts from the YouTube Data API for the last week's real uploads,
    writing them to the `performance` table. If a mistake is found after the
    fact, a human can flag it via the dashboard's `POST /correct/{story_id}`,
    which `agents/corrections_agent.py` turns into a published correction —
    see [Journalism charter](#journalism-charter).

### Story scraping & verification

- `agents/trend_detection_agent.py` scrapes Google News, Hacker News, Reddit
  (public `.rss` feeds, no OAuth needed), and ~60 mainstream/regional RSS
  feeds (World, Economy, Conflict, Science, Health, Tech, Pakistan, Asia,
  Middle East, American, Chinese outlets), dedupes by URL hash, and scores
  each story.
- `agents/verification_agent.py` computes a confidence score per story using
  cross-source confirmation, source reliability tier, keyword relevance,
  PR-language penalties, and a Pakistan-specific cross-check (requires a
  second Pakistani outlet to confirm before airing). Only stories that clear
  `min_confidence_score` (default `0.75`) are marked `verified` and eligible
  for broadcast.

  Source tiers follow the five-tier model professional newsrooms use:

  | Tier | What it covers | Examples in this codebase |
  |---|---|---|
  | 1 (highest) | Government/IGO statements, official press releases, court documents, direct interviews, live reporting, own reporters | Detected by domain (`.gov`, `.un.org`, `.who.int`, `.nato.int`), not by name — live-scraped from `news.un.org` and `gov.uk` (`trend_detection_agent.scrape_mainstream_media`); WHO News and NASA News (`.who.int`/`.gov`) also land here via domain even though they're catalogued as Tier 3 by name. No court-document, direct-interview, or live-reporting source exists yet — this pipeline only ingests published articles, not primary transcripts |
  | 2 | Major wire services & international newsrooms | Reuters, AP, AFP, BBC, Guardian, Al Jazeera, DW, Bloomberg, WSJ, Dawn, Arab News, Nikkei Asia |
  | 3 | Verified organizations — research institutes, universities, NGOs, experts | WHO, NASA, UN News, Nature, Science Daily, MIT Tech Review, IEEE Spectrum |
  | 4 | Eyewitnesses, videos, photos, social media, aggregators — only after verification | Reddit, Hacker News, Google News (aggregator — original publisher unknown), CNN, Fox News, Geo News, Xinhua, TechCrunch |
  | 5 | Rumours, anonymous posts, propaganda outlets — never publish directly | RT, Sputnik, PressTV (hard-blocked, confidence forced to 0) |

  Tier 4 sources need cross-source confirmation before a story can reach
  `verified` (Pakistani Tier 4 outlets specifically need a second Pakistani
  outlet); the `breaking` show additionally applies a stricter score floor
  (`min_score_tier4`) to Tier 4 sources given how little time there is to
  verify before airing.

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
check every 5 minutes, a daily YouTube cleanup job at 03:00, a daily
performance check at 04:00 (`agents/performance_agent.py`), and a monthly
database cleanup.

### Journalism charter

`agents/editorial_standards.py` is the single source of truth for WNX24's
nine journalism principles (Accuracy First, Truth Over Opinion, Neutral
Language, Multiple Sources, Attribution, Transparency, Accountability,
Independence, Public Interest) plus the PEMRA rules for Pakistan-related
content. Both `script_writer_agent.py` (drafting) and `compliance_agent.py`
(review) pull their rules from this one module, so they can't drift apart.
`verification_agent.py`'s cross-source confirmation is where "Multiple
Sources"/"Accuracy First" are actually enforced, not just stated.

**Accountability** ("correct mistakes publicly") is handled by
`agents/corrections_agent.py` — a human flags a broadcast via the
dashboard's `POST /correct/{story_id}` (mistakes aren't something this
pipeline can detect on its own), the agent drafts a plain, non-defensive
public correction, publishes it to the video's description and as a
top-level comment (best-effort — needs a real, non-mock upload), and
records it in the `corrections` table regardless of whether publishing
was possible.

### Agents not wired into the daily pipeline

- `agents/translation_agent.py` — LLM-backed script translation. Built and
  tested standalone but not called anywhere yet; kept as a library function
  for a future multi-language feature.
- `agents/social_media_agent.py` — deliberately a stub. Posting to X/
  Instagram/TikTok all require paid or app-reviewed developer accounts this
  project doesn't have configured; exists so `orchestrator.py` has a stable
  import target to wire in once those accounts exist.
- `agents/corrections_agent.py` — human-triggered only (dashboard endpoint),
  not part of the scheduled/automatic pipeline.

## Project layout

```
agents/            Planning, scraping, verification, scripting, review, voice, image, thumbnail, upload
  planning_agent.py        LLM story line-up selection per show
  trend_detection_agent.py Multi-source RSS scraping
  verification_agent.py    Cross-source confidence scoring
  research_agent.py        Web-research brief for analysis/explainer scripts
  web_search_agent.py      DuckDuckGo search — research_agent's search tool
  script_writer_agent.py   LLM script generation + section parsing
  compliance_agent.py      Legal/PEMRA review gate (blocks broadcast on FAIL)
  style_agent.py           House style-guide lint (informational only)
  headline_agent.py        LLM headline generation (thumbnail + video title)
  voice_agent.py           edge-tts narration per script section
  image_agent.py           Creative-Commons image sourcing
  avatar_agent.py          Legacy edge-tts + Pillow-frame video (fallback pipeline)
  thumbnail_agent.py       YouTube thumbnail rendering
  seo_agent.py             YouTube title/description/tags/chapters
  uploader_agent.py        YouTube Data API v3 upload
  performance_agent.py     Daily YouTube Analytics stats pull
  corrections_agent.py     Public correction drafting + publishing (human-triggered)
  editorial_standards.py   Journalism charter — shared by script_writer/compliance
  translation_agent.py     LLM script translation (not yet wired in)
  social_media_agent.py    Social posting (stub — no API accounts configured)
config/            Settings (dataclasses) + YouTube OAuth client/token
core/              Pydantic models + SQLite access layer
dashboard/         FastAPI "Control Room" web UI
video/             Video assembly
  video_composer.py        Primary pipeline — Ken Burns + newsroom bg + ticker
  video_editor.py           Legacy Pillow-frame renderer (avatar_agent's backend)
  heygen_client.py          Paid HeyGen avatar client (disabled by default)
scraper/           Standalone fast-cycle news watcher
utils/             Shared helpers (dirs, JSON, word counts, PKT timestamps)
tests/             pytest suite — unit tests + local-DB integration tests
assets/            Runtime assets (fonts, intro.mp4, logos, newsroom background)
  branding/        Unreferenced channel-art/marketing exports (not used by code)
orchestrator.py    Pipeline + APScheduler setup, entry points
main.py            CLI entry point
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
| `UNSPLASH_ACCESS_KEY` | No | Image agent — free-tier Unsplash source |
| `PEXELS_API_KEY` | No | Image agent — free-tier Pexels source |
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

## Development

```bash
uv sync --group dev   # installs pytest, pytest-mock, ruff on top of runtime deps
uv run pytest         # unit + local-DB integration tests (no network calls)
uv run ruff check .   # lint
```

Optional: install the pre-commit hooks so lint runs automatically before each
commit —

```bash
uv run pre-commit install
```

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


