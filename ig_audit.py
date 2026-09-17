"""
Instagram Page Audit — fetch oldest posts, store locally, run AI analysis.
Recommendations: KEEP | EDIT | DELETE with reasoning + suggested caption for EDIT.
"""
import base64
import json
import os
import re
import sqlite3
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

GRAPH      = "https://graph.facebook.com/v21.0"
TOKEN      = os.getenv("META_PAGE_TOKEN") or os.getenv("META_USER_TOKEN")
IG_ID      = os.getenv("META_IG_BUSINESS_ID")
DB         = "ig_audit.db"
IMG_DIR    = Path("ig_images")   # local image cache
IMG_DIR.mkdir(exist_ok=True)
claude     = Anthropic()

MEDIA_FIELDS = (
    "id,caption,media_type,media_url,thumbnail_url,"
    "permalink,timestamp,like_count,comments_count,is_shared_to_feed"
)


def _conn():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS ig_posts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ig_post_id       TEXT UNIQUE,
                timestamp        TEXT,
                media_type       TEXT,
                media_url        TEXT,
                thumbnail_url    TEXT,
                permalink        TEXT,
                caption          TEXT,
                like_count       INTEGER DEFAULT 0,
                comments_count   INTEGER DEFAULT 0,
                batch_offset     INTEGER DEFAULT 0,
                ai_verdict       TEXT,
                ai_reason        TEXT,
                ai_new_caption   TEXT,
                audited_at       TEXT,
                action_taken     TEXT DEFAULT 'PENDING',
                fetched_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migrations
        for migration in [
            "ALTER TABLE ig_posts ADD COLUMN batch_offset INTEGER DEFAULT 0",
            "ALTER TABLE ig_posts ADD COLUMN local_image_path TEXT",
        ]:
            try:
                db.execute(migration)
            except Exception:
                pass
        db.commit()


def _download_image(ig_post_id: str, url: str) -> str | None:
    """
    Download a single image/thumbnail to ig_images/ and return the local path.
    Returns None on failure. Skips if already downloaded.
    """
    if not url:
        return None
    # Detect extension from URL (default jpg)
    ext = ".jpg"
    for candidate in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
        if candidate in url.lower():
            ext = candidate
            break
    dest = IMG_DIR / f"{ig_post_id}{ext}"
    if dest.exists():
        return str(dest)
    try:
        r = requests.get(url, timeout=15, stream=True)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return str(dest)
    except Exception:
        pass
    return None


def _download_images_parallel(posts: list) -> dict:
    """
    Download images for a list of posts in parallel (up to 10 threads).
    Returns {ig_post_id: local_path} map.
    """
    results = {}

    def _job(p):
        pid = p["id"] if "id" in p else p.get("ig_post_id", "")
        # VIDEO posts: use thumbnail_url (JPEG) not media_url (MP4)
        if p.get("media_type") == "VIDEO":
            url = p.get("thumbnail_url") or p.get("media_url") or ""
        else:
            url = p.get("media_url") or p.get("thumbnail_url") or ""
        path = _download_image(pid, url)
        return pid, path

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_job, p): p for p in posts}
        for fut in as_completed(futures):
            try:
                pid, path = fut.result()
                if path:
                    results[pid] = path
            except Exception:
                pass
    return results


def fetch_all_posts(max_posts: int = 2000) -> list:
    """
    Paginate through all IG FEED posts (excludes archived + non-feed content).
    Returns list sorted oldest→newest.
    is_shared_to_feed=False means the post is archived or a Reels-only post — skip those.
    """
    posts = []
    url = f"{GRAPH}/{IG_ID}/media"
    params = {"fields": MEDIA_FIELDS, "limit": 100, "access_token": TOKEN}

    while url and len(posts) < max_posts:
        r = requests.get(url, params=params, timeout=30)
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", str(data["error"])))
        for p in data.get("data", []):
            # is_shared_to_feed=False → archived post or Reels-only → skip
            if p.get("is_shared_to_feed") is False:
                continue
            posts.append(p)
        url = data.get("paging", {}).get("next")
        params = {}

    posts.sort(key=lambda p: p.get("timestamp", ""))
    return posts


def store_posts(posts: list, batch_offset: int = 0) -> int:
    """
    Upsert posts into ig_posts table, then download images in parallel
    while the Instagram CDN URLs are still fresh.
    """
    # Download images first (URLs are fresh right now)
    local_paths = _download_images_parallel(posts)

    with _conn() as db:
        for p in posts:
            pid = p["id"]
            local_path = local_paths.get(pid) or ""
            db.execute("""
                INSERT INTO ig_posts
                    (ig_post_id, timestamp, media_type, media_url, thumbnail_url,
                     permalink, caption, like_count, comments_count, batch_offset,
                     local_image_path)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ig_post_id) DO UPDATE SET
                    like_count=excluded.like_count,
                    comments_count=excluded.comments_count,
                    media_url=excluded.media_url,
                    thumbnail_url=excluded.thumbnail_url,
                    batch_offset=excluded.batch_offset,
                    local_image_path=COALESCE(NULLIF(excluded.local_image_path,''), local_image_path)
            """, (
                pid,
                p.get("timestamp", ""),
                p.get("media_type", ""),
                p.get("media_url", "") or "",
                p.get("thumbnail_url", "") or "",
                p.get("permalink", ""),
                p.get("caption", "") or "",
                int(p.get("like_count", 0)),
                int(p.get("comments_count", 0)),
                batch_offset,
                local_path,
            ))
        db.commit()
    return len(posts)


def _safe_caption(text: str, max_len: int = 200) -> str:
    """Strip newlines and quotes so captions don't break prompt or JSON."""
    if not text:
        return "(no caption)"
    return text.replace("\n", " ").replace("\r", " ").replace('"', "'")[:max_len]


def _fix_json_newlines(raw: str) -> str:
    """Escape literal newlines/tabs inside JSON string values before parsing."""
    result = []
    in_string = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == '"' and (i == 0 or raw[i - 1] != '\\'):
            in_string = not in_string
            result.append(c)
        elif in_string and c == '\n':
            result.append('\\n')
        elif in_string and c == '\r':
            result.append('\\r')
        elif in_string and c == '\t':
            result.append('\\t')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _image_source(p: dict) -> dict | None:
    """
    Return a Claude image source block for this post.
    Priority: local cached file (base64) → fresh CDN URL.
    Returns None if no image available.
    """
    local = p.get("local_image_path") or ""
    if local and Path(local).exists():
        raw = Path(local).read_bytes()
        b64 = base64.standard_b64encode(raw).decode()
        # Guess media type from extension
        ext = Path(local).suffix.lower()
        mt  = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
               "webp": "image/webp"}.get(ext.lstrip("."), "image/jpeg")
        return {"type": "base64", "media_type": mt, "data": b64}

    # Fall back to CDN URL (may be expired)
    url = p.get("media_url") or p.get("thumbnail_url") or ""
    if url:
        return {"type": "url", "url": url}

    return None


def _build_vision_message(batch: list) -> list:
    """
    Build a Claude multi-modal message.
    Uses locally cached images (base64) so expired CDN URLs don't matter.
    """
    content = [
        {
            "type": "text",
            "text": (
                "You are auditing the Instagram page of Khales, a UAE luxury construction firm.\n"
                "Audience: UAE/GCC HNW individuals, 35-60, Arabic + English speakers.\n"
                "Brand pillars: precision engineering, luxury craftsmanship, bilingual presence.\n\n"
                "For each post you can see the ACTUAL IMAGE/THUMBNAIL plus engagement data.\n"
                "Judge BOTH the visual quality/brand fit AND the numbers.\n\n"
                "Verdict rules:\n"
                "- KEEP  : strong engagement (≥200 likes OR ≥10 comments) OR visually strong, on-brand image\n"
                "- EDIT  : good visual but weak caption — missing Arabic, no CTA, off-brand tone, or too short\n"
                "- DELETE: low engagement (<100 likes AND <5 comments) AND visually weak, generic, off-brand, or irrelevant\n\n"
                "Be decisive. A luxury firm should have a polished, curated feed — DELETE anything that does not belong.\n"
            ),
        }
    ]
    for idx, p in enumerate(batch, 1):
        content.append({
            "type": "text",
            "text": (
                f"\n--- POST {idx} | ID: {p['ig_post_id']} ---\n"
                f"Date: {(p.get('timestamp') or '')[:10]}  |  Type: {p.get('media_type', '')}\n"
                f"Likes: {p.get('like_count', 0)}  |  Comments: {p.get('comments_count', 0)}\n"
                f"Caption: {_safe_caption(p.get('caption', ''))}\n"
            ),
        })
        src = _image_source(p)
        if src:
            content.append({"type": "image", "source": src})

    content.append({
        "type": "text",
        "text": (
            "\nReturn ONLY a JSON array — one object per post, same order as above.\n"
            "CRITICAL: all string values must be single-line — no literal newlines.\n"
            '[{"ig_post_id":"...","verdict":"KEEP|EDIT|DELETE",'
            '"reason":"1 sentence mentioning what you saw in the image AND the engagement",'
            '"new_caption":"improved Arabic+English caption or null for KEEP/DELETE"}]'
        ),
    })
    return content


def _analyze_single_batch(batch: list) -> list:
    """Analyze one batch with Claude. Retries up to 3x on rate limit errors."""
    vision_content = _build_vision_message(batch)
    raw = None

    for attempt in range(3):
        try:
            response = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{"role": "user", "content": vision_content}],
            )
            raw = response.content[0].text.strip()
            break
        except Exception as e:
            err = str(e).lower()
            if ("rate_limit" in err or "429" in err or "overloaded" in err) and attempt < 2:
                time.sleep(10 * (attempt + 1))  # 10s, 20s backoff
                continue
            # Non-rate-limit error — try text-only fallback
            text_only = [b for b in vision_content if b["type"] == "text"]
            try:
                response = claude.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    messages=[{"role": "user", "content": text_only}],
                )
                raw = response.content[0].text.strip()
            except Exception as e2:
                print(f"[ig_audit] batch failed: {e2}")
                return [{"ig_post_id": p["ig_post_id"], "verdict": "ERROR",
                         "reason": f"Analysis error: {e2}", "new_caption": None} for p in batch]
            break

    if raw is None:
        return [{"ig_post_id": p["ig_post_id"], "verdict": "ERROR",
                 "reason": "No response after retries", "new_caption": None} for p in batch]

    try:
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = _fix_json_newlines(raw)
        batch_results = json.loads(raw)
        for r in batch_results:
            if "id" in r and "ig_post_id" not in r:
                r["ig_post_id"] = r.pop("id")
        return batch_results
    except Exception as e:
        return [{"ig_post_id": p["ig_post_id"], "verdict": "ERROR",
                 "reason": f"Parse error: {e}", "new_caption": None} for p in batch]


def analyze_posts_batch(posts: list) -> list:
    """
    Send posts to Claude (with images) for audit verdicts.
    Runs 4 batches in parallel — fast but within Anthropic rate limits.
    Failed batches get verdict=ERROR (not silently KEEP) so you can re-run them.
    """
    batch_size = 5  # keep small — each post carries a full image
    batches = [posts[i:i + batch_size] for i in range(0, len(posts), batch_size)]
    ordered = [None] * len(batches)

    with ThreadPoolExecutor(max_workers=4) as ex:
        future_to_idx = {ex.submit(_analyze_single_batch, b): i for i, b in enumerate(batches)}
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                ordered[idx] = fut.result()
            except Exception as e:
                ordered[idx] = [{"ig_post_id": p["ig_post_id"], "verdict": "ERROR",
                                  "reason": f"Worker error: {e}", "new_caption": None}
                                 for p in batches[idx]]

    return [item for batch_res in ordered if batch_res for item in batch_res]


def save_verdicts(verdicts: list):
    with _conn() as db:
        for v in verdicts:
            db.execute("""
                UPDATE ig_posts
                SET ai_verdict=?, ai_reason=?, ai_new_caption=?, audited_at=datetime('now')
                WHERE ig_post_id=?
            """, (
                v.get("verdict"),
                v.get("reason"),
                v.get("new_caption"),
                v["ig_post_id"],
            ))
        db.commit()


def run_audit(target: int = 100) -> dict:
    """Full pipeline: fetch → store → analyze → save. Returns summary."""
    init_db()
    posts = fetch_all_posts(target)
    stored = store_posts(posts)

    with _conn() as db:
        unanalyzed = [
            dict(r) for r in db.execute(
                "SELECT * FROM ig_posts WHERE ai_verdict IS NULL ORDER BY timestamp ASC"
            ).fetchall()
        ]

    verdicts = analyze_posts_batch(unanalyzed)
    save_verdicts(verdicts)

    counts = {"KEEP": 0, "EDIT": 0, "DELETE": 0}
    for v in verdicts:
        counts[v.get("verdict", "KEEP")] = counts.get(v.get("verdict", "KEEP"), 0) + 1

    return {"fetched": stored, "analyzed": len(verdicts), "verdicts": counts}


def get_audit_posts(verdict_filter: str = None, limit: int = 200,
                    offset: int = 0, batch_offset: int = None) -> list:
    init_db()
    excluded = ('DELETED', 'DISMISSED')
    with _conn() as db:
        if batch_offset is not None:
            # Show only posts from a specific fetched batch
            if verdict_filter:
                rows = db.execute(
                    "SELECT * FROM ig_posts WHERE ai_verdict=? AND action_taken NOT IN (?,?) "
                    "AND batch_offset=? ORDER BY timestamp ASC LIMIT ?",
                    (verdict_filter, *excluded, batch_offset, limit)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM ig_posts WHERE action_taken NOT IN (?,?) "
                    "AND batch_offset=? ORDER BY timestamp ASC LIMIT ?",
                    (*excluded, batch_offset, limit)
                ).fetchall()
        elif verdict_filter:
            rows = db.execute(
                "SELECT * FROM ig_posts WHERE ai_verdict=? AND action_taken NOT IN (?,?) "
                "ORDER BY timestamp ASC LIMIT ? OFFSET ?",
                (verdict_filter, *excluded, limit, offset)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM ig_posts WHERE action_taken NOT IN (?,?) "
                "ORDER BY timestamp ASC LIMIT ? OFFSET ?",
                (*excluded, limit, offset)
            ).fetchall()
    return [dict(r) for r in rows]


def get_audit_stats() -> dict:
    """Fast DB-only stats: stored count + which batch offsets are loaded."""
    init_db()
    excluded = ('DELETED', 'DISMISSED')
    with _conn() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM ig_posts WHERE action_taken NOT IN (?,?)", excluded
        ).fetchone()[0]
        offsets = [
            r[0] for r in db.execute(
                "SELECT DISTINCT batch_offset FROM ig_posts ORDER BY batch_offset"
            ).fetchall()
        ]
    return {"stored_count": count, "batch_offsets": offsets}


def dismiss_post(ig_post_id: str):
    """Remove post from audit list locally without touching Instagram API."""
    with _conn() as db:
        db.execute(
            "UPDATE ig_posts SET action_taken='DISMISSED' WHERE ig_post_id=?", (ig_post_id,)
        )
        db.commit()


def delete_ig_post(ig_post_id: str) -> dict:
    """
    Try to delete via Meta API. If that fails (permission or method error),
    dismiss locally and return the permalink so user can delete manually.
    """
    # Look up permalink first so we can return it on failure
    with _conn() as db:
        row = db.execute(
            "SELECT permalink FROM ig_posts WHERE ig_post_id=?", (ig_post_id,)
        ).fetchone()
    permalink = dict(row)["permalink"] if row else ""

    try:
        r = requests.delete(f"{GRAPH}/{ig_post_id}", params={"access_token": TOKEN}, timeout=30)
        data = r.json()
        if data.get("success"):
            with _conn() as db:
                db.execute(
                    "UPDATE ig_posts SET action_taken='DELETED' WHERE ig_post_id=?", (ig_post_id,)
                )
                db.commit()
            return {"ok": True, "api_deleted": True}
        # API returned an error — dismiss locally and tell user to delete manually
        err = (data.get("error") or {}).get("message", str(data))
        dismiss_post(ig_post_id)
        return {
            "ok": True,
            "api_deleted": False,
            "dismissed": True,
            "permalink": permalink,
            "api_error": err,
        }
    except Exception as e:
        dismiss_post(ig_post_id)
        return {
            "ok": True,
            "api_deleted": False,
            "dismissed": True,
            "permalink": permalink,
            "api_error": str(e),
        }


def update_ig_caption(ig_post_id: str, caption: str) -> dict:
    """Update post caption via Instagram API."""
    r = requests.post(
        f"{GRAPH}/{ig_post_id}",
        params={"caption": caption, "access_token": TOKEN},
        timeout=30,
    )
    data = r.json()
    if data.get("success") or "id" in data:
        with _conn() as db:
            db.execute(
                "UPDATE ig_posts SET action_taken='EDITED', caption=? WHERE ig_post_id=?",
                (caption, ig_post_id)
            )
            db.commit()
        return {"ok": True}
    return {"ok": False, "error": data.get("error", {}).get("message", str(data))}


def mark_kept(ig_post_id: str):
    with _conn() as db:
        db.execute(
            "UPDATE ig_posts SET action_taken='KEPT' WHERE ig_post_id=?", (ig_post_id,)
        )
        db.commit()


def get_top_posts(limit: int = 12) -> list:
    """Return top posts by engagement score (likes + comments×3), excluding deleted/dismissed."""
    init_db()
    excluded = ('DELETED', 'DISMISSED')
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM ig_posts WHERE action_taken NOT IN (?,?) "
            "ORDER BY (like_count + comments_count * 3) DESC LIMIT ?",
            (*excluded, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def generate_content_prompts(top_posts: list) -> list:
    """
    Claude studies top-performing posts (with vision) and returns
    6 shoot-ready content creation briefs.
    """
    if not top_posts:
        return []

    content = [
        {
            "type": "text",
            "text": (
                "You are the content strategist for Khales, a UAE luxury engineering & construction firm.\n"
                "Audience: UAE/GCC HNW individuals aged 35–60, Arabic + English speakers.\n"
                "Brand pillars: precision engineering, luxury craftsmanship, heritage + modernity.\n\n"
                "Below are the TOP PERFORMING posts from the Khales Instagram. Study each image carefully.\n"
                "Identify patterns: what visual styles, content types, and topics drive the most engagement?\n\n"
                "Then generate exactly 6 actionable content creation prompts for the next 2 weeks.\n"
                "Each prompt must be a specific brief a photographer or videographer can execute.\n"
            ),
        }
    ]

    for idx, p in enumerate(top_posts[:8], 1):
        content.append({
            "type": "text",
            "text": (
                f"\n--- TOP POST #{idx} ---\n"
                f"Type: {p.get('media_type', '')}  |  "
                f"Likes: {p.get('like_count', 0)}  |  Comments: {p.get('comments_count', 0)}\n"
                f"Caption: {_safe_caption(p.get('caption', ''), 300)}\n"
            ),
        })
        src = _image_source(p)
        if src:
            content.append({"type": "image", "source": src})

    content.append({
        "type": "text",
        "text": (
            "\nReturn ONLY a JSON array of exactly 6 content prompts. No markdown, no text outside JSON.\n"
            "All string values must be single-line (no literal newlines).\n"
            '[{"type":"VIDEO|IMAGE|CAROUSEL|REEL","concept":"short punchy title",'
            '"rationale":"1-2 sentences: why this will perform — reference patterns you saw in the top posts",'
            '"visual_direction":"specific shot/scene/setup instructions the team can execute",'
            '"copy_ar":"Arabic caption under 150 chars",'
            '"copy_en":"English caption under 150 chars",'
            '"hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5"],'
            '"cta":"LEARN_MORE|CONTACT_US|SEND_MESSAGE|SAVE|FOLLOW"}]'
        ),
    })

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = _fix_json_newlines(raw)
        return json.loads(raw)
    except Exception:
        # Retry text-only if vision fails (expired URLs)
        text_only = [b for b in content if b["type"] == "text"]
        try:
            response = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                messages=[{"role": "user", "content": text_only}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = _fix_json_newlines(raw)
            return json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"Content prompt generation failed: {e}")
