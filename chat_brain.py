"""
Central chat controller — routes natural language commands to all Khales agents.
Supports: IG Audit, Content, Platforms, Campaigns, Ad Manager.
Supports multimodal messages (image upload) via Claude vision.
"""
import json
import os
import base64
from pathlib import Path
from anthropic import Anthropic

claude = Anthropic()
UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

_last_image_path: str = ""          # last uploaded image path
_pending_campaign: dict = {}         # AI suggestion stored between proposal and execution

SYSTEM_PROMPT = """You are the Khales AI Operations Controller for Khales — a UAE luxury engineering & construction firm targeting HNW investors 35-65 in UAE/GCC.

You handle everything in this chat. The user never leaves. Match the user's language (Arabic or English).

━━ READ THE INTENT FIRST ━━

When an image is attached, determine what the user actually wants:

TYPE A — "What do you think?" / "شو رايك؟" / "Is this good?" / "Analyze this"
→ JUST give a direct visual critique. No proposals. No campaign data. No tool calls.
   Analyze: composition, lighting, luxury feel, brand alignment, what works, what doesn't.
   End with ONE question: "Want me to post it, run an ad with it, or suggest improvements?"

TYPE B — "Post this" / "انزلها" / "Schedule this" / "نشرها"
→ Analyze the image briefly, then call make_proposal with a post proposal.
   Include: caption (AR+EN), hashtags, best time. Wait for Approve.
   FORMAT RULES (non-negotiable — the tool auto-detects, do NOT override):
   - 1 image uploaded → STATIC (always)
   - Video (.mp4/.mov) uploaded → REELS (always)
   - CAROUSEL only if user explicitly uploads MULTIPLE images
   SCHEDULING RULE: NEVER schedule more than 48 hours from now.
   Best times: today or tomorrow at 7PM, 8PM, or 9PM UAE time.
   If user says "today" → today 7PM. If user says "tomorrow" → tomorrow 7PM.
   NEVER pick a date weeks or months ahead — that is always wrong.

TYPE B2 — "Post it NOW" / "نزلها هلق" / "انشرها الحين" / "نزلها مباشرة"
→ Call schedule_post_from_upload to save, then IMMEDIATELY call publish_post_now.
   Do NOT tell user to go to Schedule Posts. Do it all from here.

TYPE C — "Create a campaign" / "Run an ad" / "اعمل حملة" / "اعلن عنها"
→ Call analyze_image_for_campaign IMMEDIATELY — do NOT ask for an image.
   The tool finds the uploaded image automatically from the session.
   Even if the image was uploaded in a previous message, the tool has it.
   NEVER say "I don't have an image" — just call the tool.
   When user says "تمام" / "yes" / "اعمل" / "approve" → call execute_approved_campaign.

TYPE D — "Post it AND run an ad" / both
→ Two separate proposals, one after the other.

━━ PROPOSAL RULE ━━
NEVER create campaigns or schedule posts without make_proposal first.
NEVER show campaign lists or performance data when user just asks for image feedback.

━━ EVERYTHING ELSE ━━
- "Live/active/running campaigns" / "what's running now" / "track campaigns" → get_live_campaigns (real-time from Meta API)
- "Campaign history/performance/past" → get_campaign_performance (local DB)
- Pause/resume/sync campaigns → do immediately
- Fetch posts, run audit, generate briefs → do immediately
- Data questions → answer directly

━━ TONE ━━
Sharp, honest, no fluff. Talk like a senior creative director + media buyer.
If image quality is bad → say it directly. If it's great → say why.
"""

TOOLS = [
    {
        "name": "fetch_instagram_posts",
        "description": "Fetch latest Instagram posts from the Khales IG account and store them locally. Use when user wants to sync, refresh, or update posts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max posts to fetch (default 25)"}
            }
        }
    },
    {
        "name": "audit_instagram_posts",
        "description": "Run AI vision audit on unreviewed Instagram posts — scores quality, flags issues, suggests improvements.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_top_posts",
        "description": "Get top performing Instagram posts ranked by engagement score (likes + comments×3).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of top posts (default 12)"}
            }
        }
    },
    {
        "name": "generate_content_briefs",
        "description": "Analyze top performing posts with AI vision and generate 6 shoot-ready content creation briefs with Arabic/English copy, visual direction, hashtags, and CTA.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "research_platform",
        "description": "Fetch public profile data and generate full AI-powered content strategy for Pinterest or LinkedIn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["pinterest", "linkedin"], "description": "Which platform to research"}
            },
            "required": ["platform"]
        }
    },
    {
        "name": "sync_campaigns",
        "description": "Sync all Meta ad campaigns from all connected ad accounts and run AI analysis on performance.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_campaign_performance",
        "description": "Get Meta ad campaign performance from local DB: spend, leads, messages, CPL, status, AI verdict.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Campaigns to return (default 20)"}
            }
        }
    },
    {
        "name": "pause_campaign",
        "description": "Pause a specific Meta ad campaign by its campaign ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "The Meta campaign ID to pause"}
            },
            "required": ["campaign_id"]
        }
    },
    {
        "name": "resume_campaign",
        "description": "Resume/activate a specific Meta ad campaign by its campaign ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "The Meta campaign ID to activate"}
            },
            "required": ["campaign_id"]
        }
    },
    {
        "name": "get_audit_summary",
        "description": "Get summary stats of the Instagram audit: total posts in DB, how many reviewed/pending, action breakdown.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "download_post_images",
        "description": "Download and cache Instagram post images locally so Claude can see them during audits.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_live_campaigns",
        "description": "Get currently ACTIVE campaigns with TODAY's real-time metrics from Meta API: spend, reach, CTR, WhatsApp messages, leads. Use this when user asks about live/running/active campaigns or current performance.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "publish_post_now",
        "description": "Immediately publish a scheduled/draft post to Instagram. Use when user says 'post it now', 'نزلها', 'انشرها', 'نزلها هلق'. Pass the post_id from the schedule_post_from_upload result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer", "description": "The post ID to publish"}
            },
            "required": ["post_id"]
        }
    },
    {
        "name": "analyze_image_for_campaign",
        "description": "Analyze the uploaded image using the full AI Suggest engine (campaign history + vision), then show a proposal card to the user. Call this when user wants to create a campaign from an uploaded image.",
        "input_schema": {"type": "object", "properties": {
            "account_id": {"type": "string", "description": "Default: act_1580181188999758"}
        }}
    },
    {
        "name": "execute_approved_campaign",
        "description": "Create the campaign that was proposed and approved by the user. Call this only after the user explicitly approves the campaign proposal.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "make_proposal",
        "description": "Present a structured proposal card to the user for campaigns or posts. ALWAYS call this before creating campaigns or scheduling posts. The user will see an Approve/Reject card in the chat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["campaign", "post", "action"], "description": "Type of proposal"},
                "title": {"type": "string", "description": "Short title, e.g. 'Alkindi Villa Campaign — WhatsApp Messages'"},
                "summary": {"type": "string", "description": "1-2 sentence summary of what you're proposing and why"},
                "details": {
                    "type": "object",
                    "description": "Key-value pairs of proposal details shown to user. For campaigns: Objective, Budget/day, Caption, Audience. For posts: Caption, Format, Hashtags, Schedule."
                },
                "execute_message": {"type": "string", "description": "Exact message to auto-send when user clicks Approve, e.g. 'EXECUTE_CAMPAIGN: ...' or 'EXECUTE_POST: ...'"},
                "image_path": {"type": "string", "description": "Local path of uploaded image to show in the card (optional)"}
            },
            "required": ["type", "title", "summary", "details", "execute_message"]
        }
    },
    {
        "name": "schedule_post_from_upload",
        "description": "Schedule a post with the uploaded image/video to Instagram. Format is auto-detected: 1 image=STATIC, video=REELS. NEVER pass CAROUSEL unless user uploaded multiple images.",
        "input_schema": {
            "type": "object",
            "properties": {
                "caption": {"type": "string", "description": "Full caption with Arabic + English"},
                "hashtags": {"type": "array", "items": {"type": "string"}, "description": "List of hashtags without #"},
                "scheduled_for": {"type": "string", "description": "ISO datetime, e.g. 2026-06-01T19:00:00"},
                "tier": {"type": "string", "description": "Content tier e.g. 'project_update', 'luxury', 'general'"},
            },
            "required": ["caption", "scheduled_for"]
        }
    },
]


def _execute_tool(name: str, inputs: dict) -> tuple:
    """Execute a tool. Returns (result_dict, data_type | None)."""
    global _last_image_path, _pending_campaign
    try:
        if name == "fetch_instagram_posts":
            from ig_audit import fetch_all_posts, store_posts
            all_posts = fetch_all_posts()
            limit = inputs.get("limit", 25)
            batch = all_posts[:limit]
            stored = store_posts(batch)
            return {"success": True, "posts_stored": stored, "total_available": len(all_posts)}, None

        elif name == "audit_instagram_posts":
            from ig_audit import _conn, analyze_posts_batch, save_verdicts
            with _conn() as db:
                unanalyzed = [
                    dict(r) for r in db.execute(
                        "SELECT * FROM ig_posts WHERE ai_verdict IS NULL ORDER BY timestamp ASC LIMIT 10"
                    ).fetchall()
                ]
            if not unanalyzed:
                return {"message": "All posts already analyzed", "analyzed": 0}, None
            verdicts = analyze_posts_batch(unanalyzed)
            save_verdicts(verdicts)
            counts = {}
            for v in verdicts:
                k = v.get("verdict", "KEEP")
                counts[k] = counts.get(k, 0) + 1
            return {"analyzed": len(verdicts), "verdicts": counts}, None

        elif name == "get_top_posts":
            from ig_audit import get_top_posts
            limit = inputs.get("limit", 12)
            posts = get_top_posts(limit=limit)
            return {
                "count": len(posts),
                "posts": [
                    {
                        "id": p["ig_post_id"],
                        "caption": (p.get("caption") or "")[:80],
                        "likes": p.get("like_count", 0),
                        "comments": p.get("comments_count", 0),
                        "engagement": p.get("like_count", 0) + (p.get("comments_count", 0) * 3),
                        "media_type": p.get("media_type", ""),
                        "local_image_path": p.get("local_image_path", ""),
                        "thumbnail_url": p.get("thumbnail_url", ""),
                        "media_url": p.get("media_url", ""),
                        "permalink": p.get("permalink", ""),
                        "timestamp": (p.get("timestamp") or "")[:10],
                    }
                    for p in posts
                ]
            }, "top_posts"

        elif name == "generate_content_briefs":
            from ig_audit import get_top_posts, generate_content_prompts
            top = get_top_posts(limit=8)
            if not top:
                return {"error": "No posts in DB — fetch posts first"}, None
            briefs = generate_content_prompts(top)
            return {"count": len(briefs), "briefs": briefs}, "content_briefs"

        elif name == "research_platform":
            platform = inputs.get("platform", "").lower()
            from platform_brain import fetch_pinterest_profile, fetch_linkedin_company, generate_platform_strategy
            from ig_audit import get_top_posts
            top = get_top_posts(limit=5)
            ig_ctx = ""
            if top:
                ig_ctx = "Top performing IG posts:\n" + "\n".join(
                    f"- {p.get('media_type','')}: {(p.get('caption') or '')[:60]} | "
                    f"{p.get('like_count',0)} likes, {p.get('comments_count',0)} comments"
                    for p in top
                )
            if platform == "pinterest":
                profile = fetch_pinterest_profile()
            elif platform == "linkedin":
                profile = fetch_linkedin_company()
            else:
                return {"error": f"Unknown platform: {platform}"}, None
            strategy = generate_platform_strategy(platform, profile, ig_ctx)
            return {"platform": platform, "profile": profile, "strategy": strategy}, "platform_strategy"

        elif name == "sync_campaigns":
            from campaign_brain import sync_campaigns as _sync, init_tables, analyze_and_learn
            from ads_analysis import AD_ACCOUNTS
            init_tables()
            synced = _sync(AD_ACCOUNTS)
            analyzed = analyze_and_learn(limit=10)
            return {"campaigns_synced": synced, "analyzed": analyzed}, None

        elif name == "get_campaign_performance":
            from campaign_brain import get_campaign_history
            limit = inputs.get("limit", 20)
            campaigns = get_campaign_history(limit=limit)
            return {"count": len(campaigns), "campaigns": campaigns}, "campaigns"

        elif name == "pause_campaign":
            import ads_writer
            campaign_id = inputs.get("campaign_id", "")
            if not campaign_id:
                return {"error": "campaign_id required"}, None
            r = ads_writer.update_status(campaign_id, "PAUSED")
            return {"ok": True, "campaign_id": campaign_id, "new_status": "PAUSED", "result": r}, None

        elif name == "resume_campaign":
            import ads_writer
            campaign_id = inputs.get("campaign_id", "")
            if not campaign_id:
                return {"error": "campaign_id required"}, None
            r = ads_writer.update_status(campaign_id, "ACTIVE")
            return {"ok": True, "campaign_id": campaign_id, "new_status": "ACTIVE", "result": r}, None

        elif name == "get_audit_summary":
            from ig_audit import _conn
            with _conn() as db:
                total = db.execute("SELECT COUNT(*) FROM ig_posts").fetchone()[0]
                reviewed = db.execute("SELECT COUNT(*) FROM ig_posts WHERE ai_verdict IS NOT NULL").fetchone()[0]
                actions = db.execute(
                    "SELECT action_taken, COUNT(*) as cnt FROM ig_posts "
                    "WHERE action_taken IS NOT NULL GROUP BY action_taken"
                ).fetchall()
            return {
                "total_posts": total,
                "reviewed": reviewed,
                "pending": total - reviewed,
                "actions": {a[0]: a[1] for a in actions},
            }, None

        elif name == "download_post_images":
            from ig_audit import _conn, _download_images_parallel
            with _conn() as db:
                rows = db.execute(
                    "SELECT ig_post_id, media_url, thumbnail_url FROM ig_posts "
                    "WHERE (local_image_path IS NULL OR local_image_path='') "
                    "AND (media_url!='' OR thumbnail_url!='')"
                ).fetchall()
            posts = [{"id": r[0], "ig_post_id": r[0], "media_url": r[1] or "", "thumbnail_url": r[2] or ""} for r in rows]
            if not posts:
                return {"downloaded": 0, "message": "All images already cached"}, None
            paths = _download_images_parallel(posts)
            with _conn() as db:
                for pid, path in paths.items():
                    db.execute("UPDATE ig_posts SET local_image_path=? WHERE ig_post_id=?", (str(path), pid))
            return {"downloaded": len(paths), "total_missing": len(posts)}, None

        elif name == "get_live_campaigns":
            import requests as _req
            resp = _req.get("http://localhost:5000/api/campaigns/live", timeout=45)
            data = resp.json()
            campaigns = data if isinstance(data, list) else data.get("campaigns", data)
            if not campaigns:
                return {"message": "No active campaigns found right now.", "campaigns": []}, None
            summary = []
            for c in campaigns:
                today    = c.get("today", {})
                lifetime = c.get("lifetime", {})
                adsets   = c.get("adsets", [])
                # Aggregate adset-level today data if campaign-level is missing
                if not today and adsets:
                    for a in adsets:
                        at = a.get("today", {})
                        today = {k: today.get(k, 0) + at.get(k, 0) for k in set(today) | set(at)}
                summary.append({
                    "name":          c.get("name", ""),
                    "account":       c.get("account", ""),
                    "status":        c.get("status", ""),
                    "campaign_id":   c.get("id", ""),
                    "budget_usd":    c.get("budget_usd", 0),
                    "pct_spent":     c.get("pct_spent", 0),
                    # Today
                    "spend_today":   today.get("spend_usd", 0),
                    "reach_today":   today.get("reach", 0),
                    "impressions_today": today.get("impressions", 0),
                    "clicks_today":  today.get("clicks", 0),
                    "ctr_today":     today.get("ctr", 0),
                    "cpr_today":     today.get("cpr", 0),
                    "wa_today":      today.get("wa_convos", 0),
                    "leads_today":   today.get("leads", 0),
                    # Lifetime
                    "spend_life":    lifetime.get("spend_usd", 0),
                    "reach_life":    lifetime.get("reach", 0),
                    "ctr_life":      lifetime.get("ctr", 0),
                    "wa_life":       lifetime.get("wa_convos", 0),
                    "leads_life":    lifetime.get("leads", 0),
                    "cpr_life":      lifetime.get("cpr", 0),
                })
            return {"count": len(summary), "campaigns": summary}, "campaigns"

        elif name == "publish_post_now":
            import requests as _req
            post_id = inputs.get("post_id")
            if not post_id:
                # Try to find the most recent DRAFT post
                import sqlite3 as _sql
                db = _sql.connect("scheduled_posts.db")
                db.row_factory = _sql.Row
                row = db.execute(
                    "SELECT id FROM scheduled_posts WHERE status IN ('DRAFT','SCHEDULED') ORDER BY id DESC LIMIT 1"
                ).fetchone()
                db.close()
                if row:
                    post_id = row["id"]
                else:
                    return {"error": "No post found to publish"}, None
            resp = _req.post(f"http://localhost:5000/api/posts/{post_id}/publish-now", timeout=60)
            result = resp.json()
            return result, None

        elif name == "analyze_image_for_campaign":
            import requests as _req

            account_id = (inputs.get("account_id") or "act_1580181188999758")

            image_path = _last_image_path or ""
            if not image_path or not Path(image_path).exists():
                uploads = sorted(UPLOADS_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
                if uploads:
                    image_path = str(uploads[0])

            if not image_path or not Path(image_path).exists():
                return {"error": "No image found — please upload an image first"}, None

            with open(image_path, "rb") as img_file:
                resp = _req.post(
                    "http://localhost:5000/api/ad-manager/ai-suggest-ad",
                    data={"account_id": account_id},
                    files={"image": (Path(image_path).name, img_file)},
                    timeout=90,
                )
            suggestion = resp.json()

            if suggestion.get("error"):
                return {"error": suggestion["error"]}, None

            # Store for execution step
            _pending_campaign = {**suggestion, "_image_path": image_path, "_account_id": account_id.replace("act_", "")}

            # Return as proposal card
            return {
                "type": "campaign",
                "title": suggestion.get("campaign_name", "New Campaign"),
                "summary": suggestion.get("reasoning", "")[:250],
                "details": {
                    "Objective":    suggestion.get("objective", ""),
                    "Destination":  suggestion.get("destination_type", ""),
                    "Budget/day":   f"${suggestion.get('daily_budget_usd', 50)}",
                    "Caption":      (suggestion.get("copy") or "")[:80],
                    "Audience":     f"AE · {suggestion.get('age_min', 25)}-{suggestion.get('age_max', 65)} · {suggestion.get('gender', 'ALL')}",
                    "Placement":    suggestion.get("placement", "automatic"),
                },
                "execute_message": "تمام اعمل الحملة",
                "image_path": image_path,
            }, "proposal"

        elif name == "execute_approved_campaign":
            import requests as _req
            import ads_writer

            if not _pending_campaign:
                return {"error": "No pending campaign — analyze an image first"}, None

            suggestion   = _pending_campaign
            account_id   = suggestion.get("_account_id", "1580181188999758")
            image_path   = suggestion.get("_image_path", "")

            # Upload image to Meta
            image_hash = ""
            if image_path and Path(image_path).exists():
                image_hash = ads_writer.upload_image(account_id, image_path)

            payload = {
                "account_id":        account_id,
                "campaign_name":     suggestion.get("campaign_name", "Khales Campaign"),
                "objective":         suggestion.get("objective", "OUTCOME_ENGAGEMENT"),
                "daily_budget_usd":  suggestion.get("daily_budget_usd", 50),
                "status":            "PAUSED",
                "countries":         suggestion.get("countries", ["AE"]),
                "age_min":           suggestion.get("age_min", 25),
                "age_max":           suggestion.get("age_max", 65),
                "copy":              suggestion.get("copy", ""),
                "link":              suggestion.get("link", "https://khales.ae"),
                "image_hash":        image_hash,
                "destination_type":  suggestion.get("destination_type", "WHATSAPP"),
                "cta_type":          suggestion.get("cta_type", "LEARN_MORE"),
                "gender":            suggestion.get("gender", "ALL"),
                "device":            suggestion.get("device", "ALL"),
                "languages":         suggestion.get("languages", []),
                "placement":         suggestion.get("placement", "automatic"),
                "interest_ids":      suggestion.get("interests", []),
                "custom_audience_ids": [suggestion["recommended_audience_id"]] if suggestion.get("recommended_audience_id") else [],
            }

            resp = _req.post("http://localhost:5000/api/ad-manager/create-ad",
                             json=payload, timeout=60)
            result = resp.json()

            if result.get("error"):
                return {"error": result["error"]}, None

            _pending_campaign = {}  # clear after execution
            return {
                "ok": True,
                "campaign_id": result.get("campaign_id"),
                "ad_id":       result.get("ad_id"),
                "status":      "PAUSED — go to Ad Manager to activate",
                "message":     result.get("message", "Campaign created successfully"),
            }, None

        elif name == "make_proposal":
            proposal = {
                "type": inputs.get("type", "action"),
                "title": inputs.get("title", ""),
                "summary": inputs.get("summary", ""),
                "details": inputs.get("details", {}),
                "execute_message": inputs.get("execute_message", ""),
                "image_path": inputs.get("image_path") or "",
            }
            return proposal, "proposal"

        elif name == "schedule_post_from_upload":
            import sqlite3 as _sql
            from datetime import datetime as _dt
            import json as _json

            caption = inputs.get("caption", "")
            hashtags = inputs.get("hashtags", [])
            scheduled_for = inputs.get("scheduled_for", "")
            tier = inputs.get("tier", "general")
            image_path = inputs.get("image_path") or ""

            # Fallback: global session path → newest upload
            if not image_path or not Path(image_path).exists():
                image_path = _last_image_path or ""
            if not image_path or not Path(image_path).exists():
                uploads = sorted(UPLOADS_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
                if uploads:
                    image_path = str(uploads[0])

            # Auto-detect format — NEVER let the agent decide this
            ext = Path(image_path).suffix.lower() if image_path else ""
            if ext in (".mp4", ".mov", ".avi", ".webm"):
                fmt = "REELS"
            else:
                fmt = "IMAGE"  # single image — publisher expects "IMAGE" not "STATIC"

            # Copy image to uploads/post_media/ (served via /uploads/ route)
            # Store ONLY the relative path within uploads/ — frontend prepends /uploads/
            media_dest = Path(image_path).name  # fallback: just filename
            if image_path and Path(image_path).exists():
                import shutil
                media_dir = Path("uploads") / "post_media"
                media_dir.mkdir(exist_ok=True)
                dest = media_dir / Path(image_path).name
                shutil.copy2(image_path, dest)
                media_dest = f"post_media/{dest.name}"  # relative to uploads/

            db = _sql.connect("scheduled_posts.db")
            db.execute(
                "INSERT INTO scheduled_posts (format, media_paths, caption, hashtags, scheduled_for, status, created_at, tier) VALUES (?,?,?,?,?,?,?,?)",
                (fmt, _json.dumps([media_dest]), caption, _json.dumps(hashtags), scheduled_for, "DRAFT",
                 _dt.now().isoformat(), tier)
            )
            db.commit()
            post_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.close()

            return {
                "ok": True, "post_id": post_id,
                "status": "DRAFT — review and approve in Schedule Posts",
                "scheduled_for": scheduled_for, "format": fmt,
                "caption_preview": caption[:80],
            }, None

        else:
            return {"error": f"Unknown tool: {name}"}, None

    except Exception as e:
        return {"error": str(e)}, None


def process_message(
    message: str,
    history: list,
    image_base64: str = None,
    image_media_type: str = "image/jpeg",
    image_path: str = None,
) -> dict:
    """
    Process a chat message through Claude with tool use.
    Supports optional image attachment (base64) for vision analysis.
    history: list of {"role": "user"|"assistant", "content": str}
    Returns: {"content": str, "data": dict|None, "data_type": str|None}
    """
    global _last_image_path

    from datetime import datetime as _dt
    now_str = _dt.now().strftime("%Y-%m-%d %H:%M")  # e.g. 2026-06-01 14:45
    today_7pm = _dt.now().strftime("%Y-%m-%dT19:00:00")
    tomorrow = (_dt.now() + __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%dT19:00:00")

    if image_base64:
        # Save path globally so approval turns (which arrive without image) can find it
        if image_path:
            _last_image_path = image_path

        path_hint = f"\n[SESSION_IMAGE_SAVED: {image_path} — use analyze_image_for_campaign to build a campaign from this image, no need to ask for it again]" if image_path else ""
        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": image_base64,
                },
            },
            {"type": "text", "text": (message or "Analyze this image.") + path_hint},
        ]
    else:
        # Remind the agent about the cached image on follow-up messages
        if _last_image_path and Path(_last_image_path).exists():
            user_content = f"{message}\n[REMINDER: session image available at {_last_image_path} — call analyze_image_for_campaign directly without asking user to re-upload]"
        else:
            user_content = message

    messages = list(history) + [{"role": "user", "content": user_content}]

    system_with_date = SYSTEM_PROMPT + f"\n\nCURRENT DATE/TIME: {now_str} UAE (GMT+4)\nToday 7PM = {today_7pm}\nTomorrow 7PM = {tomorrow}\nNEVER schedule posts more than 48 hours from now."

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system_with_date,
        tools=TOOLS,
        messages=messages,
    )

    result_data = None
    result_data_type = None

    while response.stop_reason == "tool_use":
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        tool_results = []

        for tu in tool_uses:
            tool_result, data_type = _execute_tool(tu.name, tu.input)
            if data_type == "proposal":
                # Proposal always wins — overrides any previous data type
                result_data = tool_result
                result_data_type = data_type
            elif data_type and result_data_type != "proposal":
                if result_data is None:
                    result_data = tool_result
                    result_data_type = data_type
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })

        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_with_date,
            tools=TOOLS,
            messages=messages,
        )

    final_text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )

    return {
        "content": final_text,
        "data": result_data,
        "data_type": result_data_type,
    }
