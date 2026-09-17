"""SQLite store for Ad Manager: proposals, auto-rules, spend caps, action log."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_FILE = Path("ads_manager.db")


def _conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS ad_proposals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at     TEXT NOT NULL,
                proposal_type  TEXT NOT NULL,
                target_id      TEXT,
                target_name    TEXT,
                account_label  TEXT,
                summary        TEXT NOT NULL,
                reasoning      TEXT,
                payload        TEXT,
                status         TEXT NOT NULL DEFAULT 'PENDING',
                reviewed_at    TEXT,
                executed_at    TEXT,
                execution_result TEXT
            );

            CREATE TABLE IF NOT EXISTS auto_rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_key    TEXT UNIQUE NOT NULL,
                label       TEXT NOT NULL,
                description TEXT,
                enabled     INTEGER NOT NULL DEFAULT 0,
                threshold   REAL,
                action      TEXT
            );

            CREATE TABLE IF NOT EXISTS spend_caps (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                cap_key    TEXT UNIQUE NOT NULL,
                label      TEXT NOT NULL,
                amount_usd REAL NOT NULL DEFAULT 0,
                period     TEXT NOT NULL DEFAULT 'daily'
            );

            CREATE TABLE IF NOT EXISTS ad_actions_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at    TEXT NOT NULL,
                proposal_id  INTEGER,
                action_type  TEXT NOT NULL,
                target_id    TEXT,
                target_name  TEXT,
                actor        TEXT NOT NULL DEFAULT 'system',
                outcome      TEXT,
                error        TEXT
            );
        """)

        # Seed auto_rules if empty
        count = db.execute("SELECT COUNT(*) FROM auto_rules").fetchone()[0]
        if count == 0:
            rules = [
                ("pause_high_cpc", "Pause High CPC Ads",
                 "Pause ads with CPC > threshold for 3+ days", 0, 5.0, "PAUSE"),
                ("scale_low_cpc", "Scale Low-CPC Winners",
                 "Increase budget 20% for ads with CPC < threshold performing well", 0, 1.5, "SCALE"),
                ("pause_zero_clicks", "Pause Zero-Click Ads",
                 "Pause ads with 0 clicks after 3 days of spend", 0, 3.0, "PAUSE"),
                ("rebalance_budget", "Rebalance Budget Across Ad Sets",
                 "Shift budget from underperformers to top performers within campaign", 0, None, "REBALANCE"),
            ]
            db.executemany(
                "INSERT INTO auto_rules (rule_key, label, description, enabled, threshold, action) VALUES (?,?,?,?,?,?)",
                rules,
            )

        # Seed spend caps if empty
        cap_count = db.execute("SELECT COUNT(*) FROM spend_caps").fetchone()[0]
        if cap_count == 0:
            caps = [
                ("daily_total", "Daily Total Spend Cap", 500.0, "daily"),
                ("campaign_launch", "New Campaign Max Budget", 2000.0, "lifetime"),
            ]
            db.executemany(
                "INSERT INTO spend_caps (cap_key, label, amount_usd, period) VALUES (?,?,?,?)",
                caps,
            )


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("payload",):
        if key in d and d[key]:
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    return d


def create_proposal(
    proposal_type: str,
    summary: str,
    reasoning: str = "",
    target_id: str = "",
    target_name: str = "",
    account_label: str = "",
    payload: dict = None,
) -> int:
    with _conn() as db:
        cur = db.execute(
            """INSERT INTO ad_proposals
               (created_at, proposal_type, target_id, target_name, account_label,
                summary, reasoning, payload, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(),
                proposal_type,
                target_id,
                target_name,
                account_label,
                summary,
                reasoning,
                json.dumps(payload or {}),
                "PENDING",
            ),
        )
        return cur.lastrowid


def get_pending_proposals():
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM ad_proposals WHERE status='PENDING' ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_proposals(limit: int = 50):
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM ad_proposals ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def approve_proposal(proposal_id: int) -> bool:
    with _conn() as db:
        cur = db.execute(
            "UPDATE ad_proposals SET status='APPROVED', reviewed_at=? WHERE id=? AND status='PENDING'",
            (datetime.now().isoformat(), proposal_id),
        )
        return cur.rowcount > 0


def reject_proposal(proposal_id: int, reason: str = "") -> bool:
    with _conn() as db:
        cur = db.execute(
            "UPDATE ad_proposals SET status='REJECTED', reviewed_at=?, reasoning=COALESCE(reasoning,'') || ? WHERE id=? AND status='PENDING'",
            (datetime.now().isoformat(), f"\n[REJECTED] {reason}" if reason else "", proposal_id),
        )
        return cur.rowcount > 0


def mark_executed(proposal_id: int, result: dict):
    with _conn() as db:
        db.execute(
            "UPDATE ad_proposals SET status='EXECUTED', executed_at=?, execution_result=? WHERE id=?",
            (datetime.now().isoformat(), json.dumps(result), proposal_id),
        )


def mark_failed(proposal_id: int, error: str):
    with _conn() as db:
        db.execute(
            "UPDATE ad_proposals SET status='FAILED', executed_at=?, execution_result=? WHERE id=?",
            (datetime.now().isoformat(), json.dumps({"error": error}), proposal_id),
        )


def get_auto_rules():
    with _conn() as db:
        rows = db.execute("SELECT * FROM auto_rules ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def set_auto_rule(rule_key: str, enabled: bool, threshold: float = None):
    with _conn() as db:
        if threshold is not None:
            db.execute(
                "UPDATE auto_rules SET enabled=?, threshold=? WHERE rule_key=?",
                (1 if enabled else 0, threshold, rule_key),
            )
        else:
            db.execute(
                "UPDATE auto_rules SET enabled=? WHERE rule_key=?",
                (1 if enabled else 0, rule_key),
            )


def get_spend_caps():
    with _conn() as db:
        rows = db.execute("SELECT * FROM spend_caps ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def set_spend_cap(cap_key: str, amount_usd: float):
    with _conn() as db:
        db.execute(
            "UPDATE spend_caps SET amount_usd=? WHERE cap_key=?",
            (amount_usd, cap_key),
        )


def log_action(
    action_type: str,
    outcome: str,
    proposal_id: int = None,
    target_id: str = "",
    target_name: str = "",
    actor: str = "system",
    error: str = "",
):
    with _conn() as db:
        db.execute(
            """INSERT INTO ad_actions_log
               (logged_at, proposal_id, action_type, target_id, target_name, actor, outcome, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(),
                proposal_id,
                action_type,
                target_id,
                target_name,
                actor,
                outcome,
                error,
            ),
        )


def get_recent_actions(limit: int = 30):
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM ad_actions_log ORDER BY logged_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("[OK] ads_manager.db initialized")
    print(f"  auto_rules: {len(get_auto_rules())} rules seeded")
    print(f"  spend_caps: {len(get_spend_caps())} caps seeded")
