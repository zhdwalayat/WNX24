# core/database.py
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from loguru import logger

from core.models import RawStory, SourceType, VerificationStatus, VerifiedStory

DB_PATH = os.path.join("output", "wnx24_news.db")

# ── Connect ────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ── Initialise schema ──────────────────────────────────────
def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stories (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            summary         TEXT,
            url             TEXT NOT NULL,
            source          TEXT,
            source_name     TEXT,
            published_at    TEXT,
            scraped_at      TEXT,
            score           REAL DEFAULT 0,
            keywords        TEXT,
            confidence      REAL DEFAULT 0,
            cross_sources   TEXT,
            status          TEXT DEFAULT 'new',
            show_id         TEXT,
            broadcast_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS broadcasts (
            id              TEXT PRIMARY KEY,
            show_id         TEXT NOT NULL,
            story_id        TEXT NOT NULL,
            script_text     TEXT,
            word_count      INTEGER,
            youtube_url     TEXT,
            thumbnail_path  TEXT,
            broadcast_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS performance (
            video_id        TEXT PRIMARY KEY,
            show_id         TEXT,
            story_id        TEXT,
            views           INTEGER DEFAULT 0,
            likes           INTEGER DEFAULT 0,
            comments        INTEGER DEFAULT 0,
            checked_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS corrections (
            id              TEXT PRIMARY KEY,
            story_id        TEXT NOT NULL,
            show_id         TEXT,
            youtube_url     TEXT,
            original_note   TEXT,
            correction_text TEXT,
            published       INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_stories_status
            ON stories(status);
        CREATE INDEX IF NOT EXISTS idx_stories_score
            ON stories(score DESC);
        CREATE INDEX IF NOT EXISTS idx_stories_published
            ON stories(published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_stories_source
            ON stories(source_name);
        CREATE INDEX IF NOT EXISTS idx_broadcasts_show
            ON broadcasts(show_id);
    """)
    conn.commit()
    conn.close()
    logger.info(f"Database initialised — {DB_PATH}")

# ── Save stories ───────────────────────────────────────────
def save_stories(stories: list[VerifiedStory]) -> int:
    conn   = get_conn()
    saved  = 0
    now    = datetime.now(UTC).isoformat()

    for vs in stories:
        s = vs.raw
        try:
            conn.execute("""
                INSERT OR IGNORE INTO stories
                (id, title, summary, url, source, source_name,
                 published_at, scraped_at, score, keywords,
                 confidence, cross_sources, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                s.id, s.title, s.summary, s.url,
                s.source.value, s.source_name,
                s.published_at.isoformat(),
                now,
                s.score,
                json.dumps(s.keywords),
                vs.confidence_score,
                json.dumps(vs.cross_sources),
                vs.status.value,
            ))
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0:
                saved += 1
        except Exception as e:
            logger.warning(f"Could not save story {s.id}: {e}")

    conn.commit()
    conn.close()
    return saved

# ── Get fresh stories for a show ──────────────────────────
def get_fresh_stories(
    limit:          int   = 20,
    min_confidence: float = 0.7,
    source_names:   list[str] = None,
    max_age_hours:  int   = 24,
) -> list[VerifiedStory]:

    conn = get_conn()
    cutoff = datetime.now(UTC)
    from datetime import timedelta
    cutoff = (cutoff - timedelta(hours=max_age_hours)).isoformat()

    # 'pending' stories (0.4-0.75 confidence, below the "verified" bar —
    # see agents/verification_agent.py) are let into the candidate pool at
    # a fixed 0.5 floor, independent of a show's own (often higher)
    # min_confidence — this is deliberately a lower bar than "verified" so
    # genuinely cross-confirmed but lower-volume regional coverage (e.g.
    # Africa, which has fewer overlapping wire services than the US/UK/EU
    # and so scores lower even when properly confirmed) can still compete
    # for a lineup slot instead of a single confidence formula gatekeeping
    # every show. The Planning Agent and compliance gate downstream still
    # apply full editorial scrutiny to whatever gets picked.
    pending_floor = 0.5
    status_clause = "(status = 'verified' AND confidence >= ?) OR (status = 'pending' AND confidence >= ?)"

    if source_names:
        placeholders = ",".join("?" * len(source_names))
        query = f"""
            SELECT * FROM stories
            WHERE ({status_clause})
            AND published_at >= ?
            AND source_name IN ({placeholders})
            ORDER BY confidence DESC, score DESC
            LIMIT ?
        """
        rows = conn.execute(
            query,
            [min_confidence, pending_floor, cutoff] + source_names + [limit]
        ).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT * FROM stories
            WHERE ({status_clause})
            AND published_at >= ?
            ORDER BY confidence DESC, score DESC
            LIMIT ?
        """, (min_confidence, pending_floor, cutoff, limit)).fetchall()

    conn.close()
    return [_row_to_verified(row) for row in rows]

# ── Mark story as broadcast ────────────────────────────────
def mark_broadcast(
    story_id:  str,
    show_id:   str,
) -> None:
    conn = get_conn()
    now  = datetime.now(UTC).isoformat()
    conn.execute("""
        UPDATE stories
        SET status = 'broadcast',
            show_id = ?,
            broadcast_at = ?
        WHERE id = ?
    """, (show_id, now, story_id))
    conn.commit()
    conn.close()

# ── Mark story as rejected (e.g. failed compliance) ────────
def mark_rejected(story_id: str) -> None:
    """Exclude a story from all future get_fresh_stories() selection —
    used when a story clears verification but its script then fails the
    compliance gate, so the same story/script combination doesn't keep
    winning top-story selection and re-failing on every subsequent run."""
    conn = get_conn()
    conn.execute("""
        UPDATE stories
        SET status = 'rejected'
        WHERE id = ?
    """, (story_id,))
    conn.commit()
    conn.close()

# ── Save broadcast record ──────────────────────────────────
def save_broadcast(
    show_id:        str,
    story_id:       str,
    script_text:    str,
    word_count:     int,
    youtube_url:    str = "",
    thumbnail_path: str = "",
) -> None:
    import hashlib
    broadcast_id = hashlib.md5(
        f"{show_id}{story_id}{datetime.now().isoformat()}".encode()
    ).hexdigest()[:12]

    conn = get_conn()
    now  = datetime.now(UTC).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO broadcasts
        (id, show_id, story_id, script_text, word_count,
         youtube_url, thumbnail_path, broadcast_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        broadcast_id, show_id, story_id,
        script_text, word_count,
        youtube_url, thumbnail_path, now
    ))
    conn.commit()
    conn.close()

# ── Count real (non-mock) YouTube uploads in the trailing window ──
def count_real_uploads(hours: int = 24) -> int:
    conn   = get_conn()
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    row = conn.execute("""
        SELECT COUNT(*) FROM broadcasts
        WHERE broadcast_at >= ?
        AND youtube_url NOT LIKE '%MOCK%'
        AND youtube_url != ''
    """, (cutoff,)).fetchone()
    conn.close()
    return row[0]

# ── Performance Agent support ──────────────────────────────
def get_broadcasts_for_stats(days: int = 7) -> list[dict]:
    """Real (non-mock) uploads from the last `days` days, for the
    Performance Agent to pull YouTube stats for."""
    conn   = get_conn()
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT show_id, story_id, youtube_url FROM broadcasts
        WHERE broadcast_at >= ?
        AND youtube_url NOT LIKE '%MOCK%'
        AND youtube_url != ''
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def save_performance_stats(
    video_id: str,
    show_id:  str,
    story_id: str,
    views:    int,
    likes:    int,
    comments: int,
) -> None:
    conn = get_conn()
    now  = datetime.now(UTC).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO performance
        (video_id, show_id, story_id, views, likes, comments, checked_at)
        VALUES (?,?,?,?,?,?,?)
    """, (video_id, show_id, story_id, views, likes, comments, now))
    conn.commit()
    conn.close()

# ── Corrections Agent support ──────────────────────────────
def get_broadcast_by_story_id(story_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM broadcasts WHERE story_id=?
        ORDER BY broadcast_at DESC LIMIT 1
    """, (story_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def save_correction(
    story_id:        str,
    show_id:         str,
    youtube_url:     str,
    original_note:   str,
    correction_text: str,
    published:       bool,
) -> str:
    import hashlib
    correction_id = hashlib.md5(
        f"{story_id}{datetime.now().isoformat()}".encode()
    ).hexdigest()[:12]

    conn = get_conn()
    now  = datetime.now(UTC).isoformat()
    conn.execute("""
        INSERT INTO corrections
        (id, story_id, show_id, youtube_url, original_note,
         correction_text, published, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        correction_id, story_id, show_id, youtube_url,
        original_note, correction_text, int(published), now,
    ))
    conn.commit()
    conn.close()
    return correction_id

# ── Get DB stats ───────────────────────────────────────────
def get_stats() -> dict:
    conn  = get_conn()
    stats = {}
    for status in ["new", "verified", "broadcast", "rejected", "pending"]:
        row = conn.execute(
            "SELECT COUNT(*) FROM stories WHERE status=?",
            (status,)
        ).fetchone()
        stats[status] = row[0]
    stats["total_broadcasts"] = conn.execute(
        "SELECT COUNT(*) FROM broadcasts"
    ).fetchone()[0]
    conn.close()
    return stats

# ── Cleanup old DB records ─────────────────────────────────
def cleanup_old_stories(days: int = 30) -> int:
    """
    Delete stories older than N days from DB.
    Keeps DB lean and fast.
    Run monthly.
    """
    conn   = get_conn()
    cutoff = (
        datetime.now(UTC) - timedelta(days=days)
    ).isoformat()

    result = conn.execute("""
        DELETE FROM stories
        WHERE published_at < ?
        AND status IN ('broadcast', 'rejected')
    """, (cutoff,))

    deleted = result.rowcount
    conn.commit()
    conn.close()
    logger.info(
        f"DB cleanup: {deleted} old stories removed"
    )
    return deleted

# ── Row converter ──────────────────────────────────────────
def _row_to_verified(row: sqlite3.Row) -> VerifiedStory:
    from datetime import datetime
    pub = row["published_at"]
    try:
        published_at = datetime.fromisoformat(pub)
    except Exception:
        published_at = datetime.now(UTC)

    raw = RawStory(
        id           = row["id"],
        title        = row["title"],
        summary      = row["summary"] or "",
        url          = row["url"],
        source       = SourceType(row["source"] or "rss"),
        source_name  = row["source_name"] or "",
        published_at = published_at,
        score        = row["score"] or 0.0,
        keywords     = json.loads(row["keywords"] or "[]"),
    )
    return VerifiedStory(
        raw              = raw,
        confidence_score = row["confidence"] or 0.0,
        status           = VerificationStatus(row["status"] or "new"),
        cross_sources    = json.loads(row["cross_sources"] or "[]"),
    )
