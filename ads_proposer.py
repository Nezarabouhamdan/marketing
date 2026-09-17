"""Claude-powered ad proposal generator. Reads cache + strategy, scores ads, emits proposals."""
import json
import os
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

claude = Anthropic()
CACHE_FILE = Path("daily_cache.json")
STRATEGY_FILE = Path("latest_strategy.json")


def _load_context() -> str:
    parts = []

    if STRATEGY_FILE.exists():
        import re
        with open(STRATEGY_FILE, "r", encoding="utf-8") as f:
            strat = json.load(f)
        html = strat.get("strategy_html", "")
        clean = re.sub(r"<[^>]+>", " ", html).strip()
        parts.append("=== THIS WEEK'S STRATEGY ===\n" + clean[:2000])

    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

        ads_data = cache.get("ads", {})
        accounts = ads_data.get("accounts", [])
        if accounts:
            parts.append("\n=== CURRENT ADS DATA ===")
            for acct in accounts:
                if acct.get("error"):
                    parts.append(f"Account {acct['account']}: ERROR — {acct['error']}")
                    continue
                parts.append(f"\nAccount: {acct['account']} (ID: {acct.get('account_id', '?')})")
                for ad in acct.get("ads", []):
                    ins = (ad.get("insights", {}).get("data") or [{}])[0]
                    spend = float(ins.get("spend", 0))
                    impressions = int(ins.get("impressions", 0))
                    ctr = float(ins.get("ctr", 0))
                    cpc = float(ins.get("cpc", 0))
                    ds = ins.get("date_start", "")
                    dp = ins.get("date_stop", "")
                    actions = ins.get("actions", [])
                    conversions = sum(int(a.get("value", 0)) for a in actions)
                    parts.append(
                        f"  Ad: {ad.get('name', 'Unnamed')} | ID: {ad.get('id', '?')}\n"
                        f"    Status: {ad.get('status', '?')} | Campaign: {(ad.get('campaign') or {}).get('name', '?')}\n"
                        f"    Period: {ds} to {dp} | Spend: ${spend:.2f} | Impressions: {impressions:,}\n"
                        f"    CTR: {ctr:.2f}% | CPC: ${cpc:.2f} | Conversions: {conversions}\n"
                        f"    AdSet: {(ad.get('adset') or {}).get('name', '?')}"
                    )

        ig_report = cache.get("report", {})
        account = ig_report.get("account", {})
        if account:
            parts.append(
                f"\n=== INSTAGRAM CONTEXT ===\n"
                f"Followers: {account.get('followers_count', 0):,} | "
                f"Recent posts: {len(ig_report.get('posts', []))}"
            )

    return "\n".join(parts) or "(no data available)"


_SYSTEM = """You are a paid media strategist for Khales Group, a UAE luxury engineering & construction firm.
Your job is to analyze ad performance and generate a list of concrete proposals.

CRITICAL RULES:
- PAUSE proposals: ONLY for ads/campaigns with Status=ACTIVE. NEVER propose PAUSE on already PAUSED, DELETED, or ARCHIVED campaigns — that is nonsensical.
- SCALE proposals: ONLY for ads with Status=ACTIVE that are performing well.
- LAUNCH proposals: when there are no active campaigns or a gap in the strategy.
- If ALL campaigns are PAUSED/INACTIVE: do NOT suggest PAUSE. Instead suggest LAUNCH or leave the array empty.
- Each proposal must be tied to real numbers from the data.

Proposal types: PAUSE (underperforming ACTIVE ad), SCALE (increase budget on ACTIVE winner), LAUNCH (new campaign), ADJUST_AUDIENCE (refine targeting), ADJUST_CREATIVE (swap creative)

Return ONLY a valid JSON array. Each element:
{
  "proposal_type": "PAUSE|SCALE|LAUNCH|ADJUST_AUDIENCE|ADJUST_CREATIVE",
  "target_id": "meta_object_id_or_empty",
  "target_name": "human readable name",
  "account_label": "account name",
  "summary": "one sentence action",
  "reasoning": "2-3 sentences why, referencing real numbers",
  "payload": {}
}

For LAUNCH proposals, include in payload: suggested_objective, suggested_daily_budget_usd, suggested_audience_summary, suggested_creative_brief.
For SCALE proposals, include: suggested_budget_increase_pct.
For PAUSE proposals, include: current_status (must be ACTIVE).
Return an empty array [] if no strong proposals exist. Return ONLY the JSON array."""


MAX_PROPOSALS_PER_RUN = 5
STALE_AFTER_DAYS = 3


def generate_daily_proposals(cache: dict = None) -> list:
    """Generate proposals from today's data. Returns list of proposal dicts."""
    from ads_db import create_proposal, get_auto_rules
    import sqlite3

    # Step 1: Auto-reject stale PENDING proposals so they don't pile up
    try:
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = cutoff.replace(day=cutoff.day - STALE_AFTER_DAYS) if cutoff.day > STALE_AFTER_DAYS else cutoff
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=STALE_AFTER_DAYS)
        db = sqlite3.connect("ads_manager.db")
        stale_count = db.execute(
            "UPDATE ad_proposals SET status='REJECTED', reviewed_at=? "
            "WHERE status='PENDING' AND created_at < ?",
            (datetime.now().isoformat(), cutoff.isoformat())
        ).rowcount
        db.commit()
        if stale_count:
            print(f"[ads_proposer] Auto-rejected {stale_count} stale proposals (>{STALE_AFTER_DAYS} days old)")
        db.close()
    except Exception as e:
        print(f"[ads_proposer] Stale cleanup failed: {e}")

    # Step 2: Get existing PENDING (type, target_id) pairs to avoid duplicates
    existing_pending = set()
    try:
        db = sqlite3.connect("ads_manager.db")
        rows = db.execute(
            "SELECT proposal_type, target_id FROM ad_proposals WHERE status='PENDING'"
        ).fetchall()
        existing_pending = {(r[0], str(r[1] or "")) for r in rows}
        db.close()
    except Exception:
        pass

    context = _load_context()

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Today is {datetime.now().strftime('%Y-%m-%d')}.\n\n{context}"}],
        )
        text = response.content[0].text.strip()
    except Exception as e:
        return [{"error": f"Claude API error: {e}"}]

    # Strip markdown fences
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        proposals = json.loads(text)
    except json.JSONDecodeError as e:
        return [{"error": f"JSON parse error: {e}", "raw": text[:300]}]

    if not isinstance(proposals, list):
        return [{"error": "Claude returned non-list JSON"}]

    # Build a set of ACTIVE ad/campaign IDs from the cache so we can validate PAUSE proposals
    active_ids: set = set()
    try:
        if cache is None and CACHE_FILE.exists():
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        for acct in (cache or {}).get("ads", {}).get("accounts", []):
            for ad in acct.get("ads", []):
                if ad.get("status", "").upper() == "ACTIVE":
                    active_ids.add(str(ad.get("id", "")))
                    active_ids.add(str((ad.get("campaign") or {}).get("id", "")))
                    active_ids.add(str((ad.get("adset") or {}).get("id", "")))
    except Exception:
        pass

    saved_ids = []
    auto_rules = {r["rule_key"]: r for r in get_auto_rules() if r["enabled"]}

    for p in proposals:
        if "error" in p:
            continue

        ptype = p.get("proposal_type", "UNKNOWN")
        tid = str(p.get("target_id", ""))

        # Block PAUSE proposals on non-ACTIVE campaigns — this is the core guard
        if ptype == "PAUSE" and active_ids and tid not in active_ids:
            print(f"[ads_proposer] Blocked PAUSE on non-active target {tid} — skipping")
            continue

        # Step 3: Cap per run
        if len(saved_ids) >= MAX_PROPOSALS_PER_RUN:
            print(f"[ads_proposer] Reached cap of {MAX_PROPOSALS_PER_RUN} proposals — stopping")
            break

        # Step 4: Skip duplicates already sitting as PENDING
        if (ptype, tid) in existing_pending:
            print(f"[ads_proposer] Skipping duplicate PENDING: {ptype} / {tid}")
            continue

        proposal_id = create_proposal(
            proposal_type=ptype,
            summary=p.get("summary", ""),
            reasoning=p.get("reasoning", ""),
            target_id=tid,
            target_name=p.get("target_name", ""),
            account_label=p.get("account_label", ""),
            payload=p.get("payload", {}),
        )
        saved_ids.append(proposal_id)
        existing_pending.add((ptype, tid))

        _maybe_auto_execute(proposal_id, p, auto_rules)

    return saved_ids


def _maybe_auto_execute(proposal_id: int, proposal: dict, auto_rules: dict):
    """If an enabled auto-rule covers this proposal type, auto-approve and execute it."""
    ptype = proposal.get("proposal_type", "")
    triggered = False

    if ptype == "PAUSE" and "pause_high_cpc" in auto_rules:
        triggered = True
    elif ptype == "SCALE" and "scale_low_cpc" in auto_rules:
        triggered = True

    if not triggered:
        return

    try:
        from ads_db import approve_proposal
        from ads_executor import execute_proposal
        approve_proposal(proposal_id)
        execute_proposal(proposal_id)
    except Exception as e:
        print(f"[ads_proposer] auto-execute failed for proposal {proposal_id}: {e}")


if __name__ == "__main__":
    print("Generating proposals...")
    result = generate_daily_proposals()
    print(f"Created {len(result)} proposals: IDs {result}")
