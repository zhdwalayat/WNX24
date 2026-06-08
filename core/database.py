# core/database.py
import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import List, Optional
from loguru import logger
from core.models import RawStory, VerifiedStory, SourceType, VerificationStatus

DB_PATH = os.path.join("output", "mnn_news.db")

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
def save_stories(stories: List[VerifiedStory]) -> int:
    conn   = get_conn()
    saved  = 0
    now    = datetime.now(timezone.utc).isoformat()

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
    source_names:   List[str] = None,
    max_age_hours:  int   = 24,
) -> List[VerifiedStory]:

    conn = get_conn()
    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = (cutoff - timedelta(hours=max_age_hours)).isoformat()

    if source_names:
        placeholders = ",".join("?" * len(source_names))
        query = f"""
            SELECT * FROM stories
            WHERE status = 'verified'
            AND confidence >= ?
            AND published_at >= ?
            AND source_name IN ({placeholders})
            ORDER BY confidence DESC, score DESC
            LIMIT ?
        """
        rows = conn.execute(
            query,
            [min_confidence, cutoff] + source_names + [limit]
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM stories
            WHERE status = 'verified'
            AND confidence >= ?
            AND published_at >= ?
            ORDER BY confidence DESC, score DESC
            LIMIT ?
        """, (min_confidence, cutoff, limit)).fetchall()

    conn.close()
    return [_row_to_verified(row) for row in rows]

# ── Mark story as broadcast ────────────────────────────────
def mark_broadcast(
    story_id:  str,
    show_id:   str,
) -> None:
    conn = get_conn()
    now  = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE stories
        SET status = 'broadcast',
            show_id = ?,
            broadcast_at = ?
        WHERE id = ?
    """, (show_id, now, story_id))
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
    now  = datetime.now(timezone.utc).isoformat()
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

# ── Get DB stats ───────────────────────────────────────────
def get_stats() -> dict:
    conn  = get_conn()
    stats = {}
    for status in ["verified", "broadcast", "rejected", "pending"]:
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

# ── Row converter ──────────────────────────────────────────
def _row_to_verified(row: sqlite3.Row) -> VerifiedStory:
    from datetime import datetime
    pub = row["published_at"]
    try:
        published_at = datetime.fromisoformat(pub)
    except Exception:
        published_at = datetime.now(timezone.utc)

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
