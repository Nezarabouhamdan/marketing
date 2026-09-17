"""
Campaign Brain — persistent memory and intelligence for the Ads Manager agent.

Tracks every campaign that ran, generates AI learnings when campaigns end,
recommends the next campaign in the funnel, and maintains agent memory
so the system learns over time.
"""
import json
import os
import re
import sqlite3
import requests
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

claude = Anthropic()
DB_FILE = "ads_manager.db"
GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_USER_TOKEN") or os.getenv("META_PAGE_TOKEN")

# Funnel stage by objective
OBJECTIVE_TO_STAGE = {
    "OUTCOME_AWARENESS":     "AWARENESS",
    "OUTCOME_TRAFFIC":       "CONSIDERATION",
    "OUTCOME_ENGAGEMENT":    "CONSIDERATION",
    "OUTCOME_LEADS":         "CONVERSION",
    "OUTCOME_SALES":         "CONVERSION",
    "OUTCOME_APP_PROMOTION": "CONSIDERATION",
}

# What naturally follows each stage
STAGE_NEXT = {
    "AWARENESS": [
        {"destination_type": "WHATSAPP",   "objective": "OUTCOME_ENGAGEMENT", "label": "WhatsApp Retargeting",  "why": "Warm audience → move them to a conversation"},
        {"destination_type": "WEBSITE",    "objective": "OUTCOME_TRAFFIC",    "label": "Traffic Campaign",      "why": "Drive website visits from aware audience"},
    ],
    "CONSIDERATION": [
        {"destination_type": "LEAD_FORM",  "objective": "OUTCOME_LEADS",      "label": "Lead Form Campaign",    "why": "Capture leads from engaged audience"},
        {"destination_type": "WHATSAPP",   "objective": "OUTCOME_ENGAGEMENT", "label": "WhatsApp Conversion",   "why": "Push engaged audience to start a WhatsApp conversation"},
    ],
    "CONVERSION": [
        {"destination_type": "WEBSITE",    "objective": "OUTCOME_AWARENESS",  "label": "New Awareness Push",    "why": "Refresh top of funnel with fresh audience"},
        {"destination_type": "LEAD_FORM",  "objective": "OUTCOME_LEADS",      "label": "Lookalike Lead Campaign","why": "Target lookalike of converted leads"},
    ],
}


def _conn():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_tables():
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS campaign_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_campaign_id TEXT UNIQUE,
                name TEXT,
                account_id TEXT,
                objective TEXT,
                destination_type TEXT,
                status TEXT,
                funnel_stage TEXT,
                spend_usd REAL DEFAULT 0,
                impressions INTEGER DEFAULT 0,
                reach INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                ctr REAL DEFAULT 0,
                cpc REAL DEFAULT 0,
                leads INTEGER DEFAULT 0,
                messages INTEGER DEFAULT 0,
                start_date TEXT,
                end_date TEXT,
                ai_verdict TEXT,
                ai_notes TEXT,
                next_step_json TEXT,
                synced_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                memory_key TEXT UNIQUE,
                content TEXT NOT NULL,
                source TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        db.commit()


def sync_campaigns(ad_accounts: list) -> int:
    """Pull all campaigns from Meta API into campaign_memory."""
    synced = 0
    for _label, account_id in ad_accounts:
        if not account_id:
            continue
        raw_id = account_id.replace("act_", "")
        url = f"{GRAPH}/{account_id}/campaigns"
        params = {
            "fields": (
                "id,name,objective,status,start_time,stop_time,"
                "insights.date_preset(maximum){"
                "spend,impressions,reach,clicks,ctr,cpc,actions,date_start,date_stop}"
            ),
            "limit": 100,
            "access_token": TOKEN,
        }
        try:
            r = requests.get(url, params=params, timeout=30)
            data = r.json()
            if "error" in data:
                continue
            campaigns = data.get("data", [])
            with _conn() as db:
                for c in campaigns:
                    ins = (c.get("insights", {}).get("data") or [{}])[0]
                    actions = ins.get("actions", []) or []
                    LEAD_TYPES = {
                        "lead",
                        "onsite_conversion.lead_grouped",
                        "offsite_complete_registration_add_meta_leads",
                        "offsite_search_add_meta_leads",
                    }
                    leads = sum(
                        float(a["value"]) for a in actions
                        if a.get("action_type") in LEAD_TYPES
                    )
                    # Use conversations_started as the primary WhatsApp metric
                    messages = sum(
                        float(a["value"]) for a in actions
                        if a.get("action_type") in (
                            "onsite_conversion.messaging_conversation_started_7d",
                            "onsite_conversion.total_messaging_connection",
                        )
                    )
                    objective = c.get("objective", "")
                    stage = OBJECTIVE_TO_STAGE.get(objective, "AWARENESS")
                    db.execute("""
                        INSERT INTO campaign_memory
                            (meta_campaign_id, name, account_id, objective, status, funnel_stage,
                             spend_usd, impressions, reach, clicks, ctr, cpc, leads, messages,
                             start_date, end_date, synced_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(meta_campaign_id) DO UPDATE SET
                            status=excluded.status,
                            spend_usd=excluded.spend_usd,
                            impressions=excluded.impressions,
                            reach=excluded.reach,
                            clicks=excluded.clicks,
                            ctr=excluded.ctr,
                            cpc=excluded.cpc,
                            leads=excluded.leads,
                            messages=excluded.messages,
                            end_date=excluded.end_date,
                            synced_at=excluded.synced_at
                    """, (
                        c["id"], c.get("name"), raw_id, objective, c.get("status"), stage,
                        float(ins.get("spend", 0)), int(ins.get("impressions", 0)),
                        int(ins.get("reach", 0)), int(ins.get("clicks", 0)),
                        float(ins.get("ctr", 0)), float(ins.get("cpc", 0)),
                        int(leads), int(messages),
                        (c.get("start_time") or "")[:10] or None,
                        (c.get("stop_time") or "")[:10] or None,
                        datetime.now().isoformat(),
                    ))
                    synced += 1
                db.commit()
        except Exception as e:
            print(f"[campaign_brain] sync error for {account_id}: {e}")
    return synced


def analyze_and_learn(limit: int = 5) -> int:
    """
    Find campaigns without AI notes yet (min $10 spend).
    Generate verdict + learning + next-step recommendation. Store in agent memory.
    """
    with _conn() as db:
        rows = db.execute("""
            SELECT * FROM campaign_memory
            WHERE ai_notes IS NULL AND spend_usd > 10
            ORDER BY synced_at DESC LIMIT ?
        """, (limit,)).fetchall()
        rows = [dict(r) for r in rows]
        memories = db.execute(
            "SELECT content FROM agent_memory WHERE agent='ads_manager' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

    if not rows:
        return 0

    memory_context = "\n".join(m["content"] for m in memories) if memories else "No prior memories yet."
    analyzed = 0

    for row in rows:
        stage = row.get("funnel_stage", "AWARENESS")
        next_options = STAGE_NEXT.get(stage, STAGE_NEXT["AWARENESS"])
        prompt = f"""You are the Ads Manager AI for Khales, a UAE luxury construction firm.

PRIOR LEARNINGS FROM PAST CAMPAIGNS:
{memory_context}

CAMPAIGN TO ANALYZE:
Name: {row['name']}
Objective: {row['objective']} (Funnel stage: {stage})
Status: {row['status']}
Dates: {row['start_date']} → {row['end_date'] or 'ongoing'}
Spend: ${row['spend_usd']:.2f} | Impressions: {row['impressions']:,} | Reach: {row['reach']:,}
Clicks: {row['clicks']:,} | CTR: {row['ctr']:.2f}% | CPC: ${row['cpc']:.2f}
Leads generated: {row['leads']} | WhatsApp conversations started: {row['messages']}

NEXT FUNNEL OPTIONS (pick the most logical one given performance):
{json.dumps(next_options, indent=2)}

Return ONLY this JSON:
{{
  "verdict": "WINNER" | "UNDERPERFORMER" | "LEARNING",
  "learning": "1-2 sentence specific insight referencing actual numbers",
  "next_step_label": "exact label from the options above",
  "next_step_destination_type": "WEBSITE|WHATSAPP|LEAD_FORM",
  "next_step_objective": "OUTCOME_...",
  "next_step_why": "why this specific next step given the numbers"
}}"""
        try:
            response = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            result = json.loads(raw)

            next_step = {
                "label":            result.get("next_step_label", ""),
                "destination_type": result.get("next_step_destination_type", "WEBSITE"),
                "objective":        result.get("next_step_objective", "OUTCOME_AWARENESS"),
                "why":              result.get("next_step_why", ""),
            }
            verdict  = result.get("verdict", "LEARNING")
            learning = result.get("learning", "")

            with _conn() as db:
                db.execute(
                    "UPDATE campaign_memory SET ai_verdict=?, ai_notes=?, next_step_json=? WHERE id=?",
                    (verdict, learning, json.dumps(next_step), row["id"])
                )
                db.execute("""
                    INSERT INTO agent_memory (agent, memory_key, content, source)
                    VALUES ('ads_manager', ?, ?, ?)
                    ON CONFLICT(memory_key) DO UPDATE SET content=excluded.content, updated_at=datetime('now')
                """, (
                    f"campaign_{row['meta_campaign_id']}",
                    f"[{verdict}] {row['name']}: {learning}",
                    row["meta_campaign_id"],
                ))
                db.commit()
            analyzed += 1
        except Exception as e:
            print(f"[campaign_brain] analyze error for {row.get('id')}: {e}")

    return analyzed


def get_campaign_history(limit: int = 30) -> list:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM campaign_memory ORDER BY synced_at DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("next_step_json"):
            try:
                d["next_step"] = json.loads(d["next_step_json"])
            except Exception:
                d["next_step"] = None
        result.append(d)
    return result


def get_agent_memories(agent: str = "ads_manager", limit: int = 20) -> list:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM agent_memory WHERE agent=? ORDER BY created_at DESC LIMIT ?",
            (agent, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def get_next_step_recommendation() -> dict:
    """Return the recommended next campaign based on most recent campaign + funnel logic."""
    with _conn() as db:
        latest = db.execute("""
            SELECT * FROM campaign_memory
            WHERE next_step_json IS NOT NULL
            ORDER BY synced_at DESC LIMIT 1
        """).fetchone()
        total = db.execute("SELECT COUNT(*) FROM campaign_memory").fetchone()[0]

    if not latest or total == 0:
        return {
            "label": "Start with Awareness",
            "destination_type": "WEBSITE",
            "objective": "OUTCOME_AWARENESS",
            "why": "No campaign history yet — top of funnel is the right starting point for Khales",
            "based_on": None,
        }

    latest = dict(latest)
    next_step = json.loads(latest.get("next_step_json", "{}"))
    return {
        **next_step,
        "based_on": latest["name"],
        "based_on_verdict": latest.get("ai_verdict"),
        "based_on_learning": latest.get("ai_notes"),
        "based_on_stage": latest.get("funnel_stage"),
    }


def save_memory(agent: str, key: str, content: str, source: str = "") -> None:
    """Any agent can call this to persist a learning."""
    with _conn() as db:
        db.execute("""
            INSERT INTO agent_memory (agent, memory_key, content, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(memory_key) DO UPDATE SET content=excluded.content, updated_at=datetime('now')
        """, (agent, key, content, source))
        db.commit()
