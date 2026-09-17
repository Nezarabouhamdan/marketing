"""SQLite store for scheduled Instagram posts."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("scheduled_posts.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                format          TEXT    NOT NULL,
                media_paths     TEXT    NOT NULL,
                caption         TEXT    NOT NULL,
                hashtags        TEXT    DEFAULT '[]',
                scheduled_for   TEXT    NOT NULL,
                status          TEXT    DEFAULT 'DRAFT',
                ig_media_id     TEXT,
                ig_permalink    TEXT,
                error_message   TEXT,
                created_at      TEXT,
                published_at    TEXT,
                tier            TEXT    DEFAULT 'general'
            );
        """)


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    # Deserialize JSON columns
    for col in ("media_paths", "hashtags"):
        if isinstance(d.get(col), str):
            try:
                d[col] = json.loads(d[col])
            except Exception:
                pass
    return d


def create_post(format, media_paths, caption, hashtags, scheduled_for, tier="general"):
    with _conn() as db:
        cur = db.execute("""
            INSERT INTO scheduled_posts
                (format, media_paths, caption, hashtags, scheduled_for, status, created_at, tier)
            VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?)
        """, (
            format,
            json.dumps(media_paths) if isinstance(media_paths, list) else media_paths,
            caption,
            json.dumps(hashtags) if isinstance(hashtags, list) else hashtags,
            scheduled_for,
            datetime.now(timezone.utc).isoformat(),
            tier,
        ))
        return cur.lastrowid


def update_post(post_id, **fields):
    if not fields:
        return
    # Serialize any list values
    for k, v in fields.items():
        if isinstance(v, list):
            fields[k] = json.dumps(v)
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [post_id]
    with _conn() as db:
        db.execute(f"UPDATE scheduled_posts SET {cols} WHERE id = ?", vals)


def get_post(post_id):
    with _conn() as db:
        row = db.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()
        return _row_to_dict(row)


def list_posts(status=None, limit=50):
    with _conn() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM scheduled_posts WHERE status = ? ORDER BY scheduled_for DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM scheduled_posts ORDER BY scheduled_for DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_due_posts():
    """Return SCHEDULED posts whose scheduled_for time has passed."""
    now = datetime.now(timezone.utc)
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'SCHEDULED'"
        ).fetchall()
        due = []
        for row in rows:
            try:
                sf = row["scheduled_for"]
                post_time = datetime.fromisoformat(sf)
                if post_time.tzinfo is None:
                    # Assume Dubai (UTC+4) if no offset stored
                    from datetime import timedelta
                    post_time = post_time.replace(tzinfo=timezone(timedelta(hours=4)))
                if post_time <= now:
                    due.append(_row_to_dict(row))
            except Exception:
                pass
        return due


def mark_publishing(post_id):
    update_post(post_id, status="PUBLISHING")


def mark_published(post_id, ig_media_id, ig_permalink):
    update_post(
        post_id,
        status="PUBLISHED",
        ig_media_id=ig_media_id,
        ig_permalink=ig_permalink,
        published_at=datetime.now(timezone.utc).isoformat(),
        error_message=None,
    )


def mark_failed(post_id, error):
    update_post(post_id, status="FAILED", error_message=str(error)[:1000])


if __name__ == "__main__":
    init_db()
    print(f"[OK] posts_db initialised at {DB_PATH.resolve()}")
