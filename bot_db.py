"""SQLite store for bot conversations, messages, and comments."""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("bot_conversations.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL UNIQUE,
                username TEXT,
                last_msg_at TEXT,
                language TEXT DEFAULT 'en',
                status TEXT DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_message_id TEXT UNIQUE,
                conversation_id INTEGER REFERENCES conversations(id),
                direction TEXT NOT NULL,
                text TEXT,
                replied_with TEXT,
                language TEXT DEFAULT 'en',
                created_at TEXT,
                needs_human INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_comment_id TEXT UNIQUE,
                post_id TEXT,
                user_id TEXT,
                username TEXT,
                comment_text TEXT,
                replied INTEGER DEFAULT 0,
                replied_with TEXT,
                language TEXT DEFAULT 'en',
                created_at TEXT,
                needs_human INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                user_id TEXT NOT NULL,
                sent_at TEXT NOT NULL
            );
        """)


def _upsert_conversation(db, user_id, username, lang, status="active"):
    db.execute("""
        INSERT INTO conversations (platform, user_id, username, last_msg_at, language, status)
        VALUES ('instagram', ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            last_msg_at = excluded.last_msg_at,
            language = excluded.language,
            status = excluded.status
    """, (user_id, username or user_id, datetime.utcnow().isoformat(), lang, status))
    row = db.execute("SELECT id FROM conversations WHERE user_id = ?", (user_id,)).fetchone()
    return row["id"]


def log_dm(user_id, username, msg, direction, reply_text=None, lang="en",
           needs_human=False, meta_message_id=None):
    """Log an inbound or outbound DM. Returns False if meta_message_id already seen."""
    with _conn() as db:
        if meta_message_id and direction == "inbound":
            exists = db.execute(
                "SELECT id FROM messages WHERE meta_message_id = ?", (meta_message_id,)
            ).fetchone()
            if exists:
                return False  # already processed — idempotency guard

        status = "needs_human" if needs_human else "active"
        conv_id = _upsert_conversation(db, user_id, username, lang, status)

        db.execute("""
            INSERT OR IGNORE INTO messages
                (meta_message_id, conversation_id, direction, text, replied_with, language, created_at, needs_human)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            meta_message_id, conv_id, direction, msg, reply_text,
            lang, datetime.utcnow().isoformat(), int(needs_human)
        ))
    return True


def log_comment(post_id, user_id, username, comment, reply=None, lang="en",
                needs_human=False, meta_comment_id=None):
    """Log a comment + optional reply. Returns False if already processed."""
    with _conn() as db:
        if meta_comment_id:
            exists = db.execute(
                "SELECT id FROM comments WHERE meta_comment_id = ?", (meta_comment_id,)
            ).fetchone()
            if exists:
                return False

        db.execute("""
            INSERT OR IGNORE INTO comments
                (meta_comment_id, post_id, user_id, username, comment_text, replied,
                 replied_with, language, created_at, needs_human)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            meta_comment_id, post_id, user_id, username, comment,
            1 if reply else 0, reply, lang, datetime.utcnow().isoformat(), int(needs_human)
        ))
    return True


def check_rate_limit(user_id, window_seconds=300, max_replies=3):
    """Return True if user is within limit, False if they've hit the cap."""
    cutoff = (datetime.utcnow().timestamp() - window_seconds)
    with _conn() as db:
        count = db.execute("""
            SELECT COUNT(*) as n FROM rate_limits
            WHERE user_id = ? AND CAST(strftime('%s', sent_at) AS INTEGER) > ?
        """, (user_id, int(cutoff))).fetchone()["n"]

        if count >= max_replies:
            return False

        db.execute(
            "INSERT INTO rate_limits (user_id, sent_at) VALUES (?, ?)",
            (user_id, datetime.utcnow().isoformat())
        )
        # Prune old rate limit rows to keep DB small
        db.execute("""
            DELETE FROM rate_limits
            WHERE CAST(strftime('%s', sent_at) AS INTEGER) < ?
        """, (int(cutoff),))
    return True


def get_recent_conversations(limit=50):
    with _conn() as db:
        rows = db.execute("""
            SELECT c.*, m.text AS last_message, m.replied_with AS last_reply
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
                AND m.id = (SELECT MAX(id) FROM messages WHERE conversation_id = c.id AND direction = 'inbound')
            ORDER BY c.last_msg_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_flagged():
    with _conn() as db:
        msgs = db.execute("""
            SELECT m.*, c.username, c.user_id
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.needs_human = 1 AND m.direction = 'inbound'
            ORDER BY m.created_at DESC
            LIMIT 100
        """).fetchall()
        coms = db.execute("""
            SELECT * FROM comments WHERE needs_human = 1
            ORDER BY created_at DESC LIMIT 100
        """).fetchall()
        return {
            "messages": [dict(r) for r in msgs],
            "comments": [dict(r) for r in coms],
        }


def mark_replied(message_id):
    with _conn() as db:
        db.execute("UPDATE messages SET needs_human = 0 WHERE id = ?", (message_id,))


if __name__ == "__main__":
    init_db()
    print(f"[OK] Database initialised at {DB_PATH.resolve()}")
