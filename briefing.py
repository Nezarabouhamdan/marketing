"""
Daily briefing engine — aggregates actionable alerts from all agents.
Surfaces what needs attention: campaign issues, pending content, audit backlog, inbox escalations.
"""
import sqlite3
from datetime import datetime, timedelta


def _db(path: str):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def get_briefing() -> dict:
    alerts = []

    # ── 1. Campaign alerts ─────────────────────────────────────────────────────
    try:
        db = _db("ads_manager.db")

        # UNDERPERFORMER campaigns still active
        rows = db.execute("""
            SELECT name, spend_usd, leads, messages, meta_campaign_id
            FROM campaign_memory
            WHERE ai_verdict = 'UNDERPERFORMER'
            AND status NOT IN ('PAUSED', 'DELETED', 'ARCHIVED')
            ORDER BY spend_usd DESC LIMIT 3
        """).fetchall()
        for r in rows:
            results = (r["leads"] or 0) + (r["messages"] or 0)
            alerts.append({
                "type": "critical",
                "category": "campaigns",
                "icon": "🔴",
                "title": f"Underperformer running: {r['name'][:42]}",
                "detail": f"${r['spend_usd']:.0f} spent · {results} results · AI flagged to pause",
                "action_label": "Pause it",
                "action_chat": f"Pause campaign {r['meta_campaign_id']}",
            })

        # High spend + zero results (not already flagged)
        rows = db.execute("""
            SELECT name, spend_usd, leads, messages, meta_campaign_id
            FROM campaign_memory
            WHERE spend_usd > 80
              AND (COALESCE(leads,0) + COALESCE(messages,0)) = 0
              AND ai_verdict != 'UNDERPERFORMER'
            ORDER BY spend_usd DESC LIMIT 2
        """).fetchall()
        for r in rows:
            alerts.append({
                "type": "warning",
                "category": "campaigns",
                "icon": "⚠️",
                "title": f"${r['spend_usd']:.0f} spent, zero results",
                "detail": f"{r['name'][:50]} — no leads or messages yet",
                "action_label": "Review",
                "action_tab": "admanager",
            })

        # WINNER campaigns — surface for scaling
        winners = db.execute("""
            SELECT name, spend_usd, leads, messages, cpl
            FROM campaign_memory
            WHERE ai_verdict = 'WINNER'
            ORDER BY (COALESCE(leads,0) + COALESCE(messages,0)) DESC LIMIT 2
        """).fetchall()
        for r in winners:
            results = (r["leads"] or 0) + (r["messages"] or 0)
            if results > 0:
                alerts.append({
                    "type": "success",
                    "category": "campaigns",
                    "icon": "🏆",
                    "title": f"Winner campaign: {r['name'][:42]}",
                    "detail": f"{results} results · ${r['spend_usd']:.0f} spend · CPL ${r['cpl'] or 0:.0f}",
                    "action_label": "Scale it",
                    "action_chat": f"How should I scale the campaign '{r['name']}'?",
                })
            break  # show only top winner

        # Pending ad proposals
        pending = db.execute(
            "SELECT COUNT(*) as cnt FROM ad_proposals WHERE status='PENDING'"
        ).fetchone()["cnt"]
        if pending > 0:
            oldest = db.execute(
                "SELECT MIN(created_at) as oldest FROM ad_proposals WHERE status='PENDING'"
            ).fetchone()["oldest"]
            age_txt = ""
            if oldest:
                try:
                    delta = datetime.now() - datetime.fromisoformat(oldest)
                    age_txt = f" · {delta.days}d old" if delta.days > 0 else " · just created"
                except Exception:
                    pass
            alerts.append({
                "type": "info",
                "category": "ads",
                "icon": "📋",
                "title": f"{pending} ad proposal{'s' if pending > 1 else ''} waiting for review{age_txt}",
                "detail": "Agent has campaign suggestions ready — approve or reject",
                "action_label": "Review",
                "action_tab": "admanager",
            })

        db.close()
    except Exception:
        pass

    # ── 2. Scheduled posts alerts ──────────────────────────────────────────────
    try:
        db = _db("scheduled_posts.db")

        failed = db.execute(
            "SELECT COUNT(*) as cnt FROM scheduled_posts WHERE status='FAILED'"
        ).fetchone()["cnt"]
        if failed > 0:
            alerts.append({
                "type": "critical",
                "category": "content",
                "icon": "❌",
                "title": f"{failed} post{'s' if failed > 1 else ''} failed to publish",
                "detail": "Publishing errors — needs retry or manual fix",
                "action_label": "Fix now",
                "action_tab": "schedule",
            })

        # Draft posts not yet approved
        drafts = db.execute(
            "SELECT COUNT(*) as cnt FROM scheduled_posts WHERE status='DRAFT'"
        ).fetchone()["cnt"]
        if drafts > 0:
            alerts.append({
                "type": "warning",
                "category": "content",
                "icon": "📝",
                "title": f"{drafts} draft post{'s' if drafts > 1 else ''} not yet approved",
                "detail": "Review and approve to get them into the publishing queue",
                "action_label": "Approve",
                "action_tab": "schedule",
            })

        # Nothing scheduled for next 48h
        tomorrow = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        due_soon = db.execute(
            "SELECT COUNT(*) as cnt FROM scheduled_posts WHERE status='SCHEDULED' AND DATE(scheduled_for) <= ?",
            (tomorrow,)
        ).fetchone()["cnt"]
        if due_soon == 0:
            alerts.append({
                "type": "warning",
                "category": "content",
                "icon": "📅",
                "title": "No posts scheduled for next 48 hours",
                "detail": "Content gap — use the agent to generate and schedule posts",
                "action_label": "Generate",
                "action_chat": "Generate content briefs for this week",
            })

        db.close()
    except Exception:
        pass

    # ── 3. Instagram Audit alerts ──────────────────────────────────────────────
    try:
        db = sqlite3.connect("ig_audit.db")
        db.row_factory = sqlite3.Row

        total = db.execute("SELECT COUNT(*) FROM ig_posts").fetchone()[0]
        unreviewed = db.execute(
            "SELECT COUNT(*) FROM ig_posts WHERE ai_verdict IS NULL"
        ).fetchone()[0]
        flagged_delete = db.execute(
            "SELECT COUNT(*) FROM ig_posts "
            "WHERE ai_verdict='DELETE' AND (action_taken IS NULL OR action_taken='')"
        ).fetchone()[0]
        flagged_optimize = db.execute(
            "SELECT COUNT(*) FROM ig_posts "
            "WHERE ai_verdict='OPTIMIZE' AND (action_taken IS NULL OR action_taken='')"
        ).fetchone()[0]

        if total == 0:
            alerts.append({
                "type": "info",
                "category": "audit",
                "icon": "📥",
                "title": "No Instagram posts loaded yet",
                "detail": "Fetch your posts to start the AI content audit",
                "action_label": "Fetch posts",
                "action_chat": "Fetch my latest Instagram posts",
            })
        else:
            if unreviewed > 5:
                alerts.append({
                    "type": "info",
                    "category": "audit",
                    "icon": "🔎",
                    "title": f"{unreviewed} posts not yet audited",
                    "detail": "Run the AI vision audit to score and flag your content",
                    "action_label": "Run audit",
                    "action_chat": "Run Instagram audit",
                })
            if flagged_delete > 0:
                alerts.append({
                    "type": "warning",
                    "category": "audit",
                    "icon": "🗑️",
                    "title": f"{flagged_delete} post{'s' if flagged_delete > 1 else ''} flagged for deletion",
                    "detail": "AI recommended removing these — review and confirm",
                    "action_label": "Review",
                    "action_tab": "igaudit",
                })
            if flagged_optimize > 0:
                alerts.append({
                    "type": "info",
                    "category": "audit",
                    "icon": "✏️",
                    "title": f"{flagged_optimize} post{'s' if flagged_optimize > 1 else ''} need caption updates",
                    "detail": "AI has improved captions ready to apply",
                    "action_label": "Review",
                    "action_tab": "igaudit",
                })

        db.close()
    except Exception:
        pass

    # ── 4. Strategy compliance ─────────────────────────────────────────────────
    try:
        import json as _json, re as _re
        from pathlib import Path as _P
        sf = _P("latest_strategy.json")
        if sf.exists():
            strat = _json.loads(sf.read_text(encoding="utf-8"))
            html = strat.get("strategy_html", "")
            DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            now = datetime.now()
            week_start = now - timedelta(days=now.weekday())
            week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            rows = _re.findall(r"<tr[^>]*>(.*?)</tr>", html, _re.DOTALL)
            tasks = []
            for row in rows[1:]:
                cells = _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, _re.DOTALL)
                cells = [_re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                if len(cells) >= 3 and cells[0].strip():
                    day_key = cells[0].strip()[:3].lower()
                    day_num = DAY_MAP.get(day_key)
                    if day_num is not None:
                        target = (week_start + timedelta(days=day_num)).strftime("%Y-%m-%d")
                        tasks.append({
                            "day": cells[0], "target_date": target,
                            "format": cells[1] if len(cells) > 1 else "",
                            "topic": cells[2] if len(cells) > 2 else "",
                        })

            # Check scheduled posts for compliance
            pending_tasks = []
            if tasks:
                db_sp = sqlite3.connect("scheduled_posts.db")
                db_sp.row_factory = sqlite3.Row
                scheduled_dates = {
                    row["scheduled_for"][:10]
                    for row in db_sp.execute(
                        "SELECT scheduled_for FROM scheduled_posts WHERE status NOT IN ('CANCELLED','FAILED')"
                    ).fetchall()
                }
                db_sp.close()
                for t in tasks:
                    if t["target_date"] not in scheduled_dates:
                        pending_tasks.append(t)

            if len(pending_tasks) >= 3:
                fmt_list = ", ".join(
                    f"{t['day']} {t['format']}" for t in pending_tasks[:3]
                )
                alerts.append({
                    "type": "warning",
                    "category": "strategy",
                    "icon": "📋",
                    "title": f"{len(pending_tasks)} strategy posts not scheduled",
                    "detail": fmt_list,
                    "action_label": "Schedule",
                    "action_tab": "schedule",
                })
            elif len(pending_tasks) > 0:
                # Check if any are today
                today = now.strftime("%Y-%m-%d")
                today_task = next((t for t in pending_tasks if t["target_date"] == today), None)
                if today_task:
                    alerts.append({
                        "type": "critical",
                        "category": "strategy",
                        "icon": "📌",
                        "title": f"Today's post not scheduled: {today_task['format']}",
                        "detail": today_task["topic"][:80],
                        "action_label": "Schedule now",
                        "action_tab": "schedule",
                    })
    except Exception:
        pass

    # Sort: critical first, then warning, then success, then info
    order = {"critical": 0, "warning": 1, "success": 2, "info": 3}
    alerts.sort(key=lambda a: order.get(a["type"], 4))

    return {
        "alerts": alerts,
        "count": len(alerts),
        "generated_at": datetime.now().isoformat(),
    }
