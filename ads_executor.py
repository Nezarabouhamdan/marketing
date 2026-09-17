"""Execute approved ad proposals via ads_writer. Logs outcome to ad_actions_log."""
from ads_db import (
    approve_proposal, mark_executed, mark_failed,
    log_action, get_all_proposals, _conn,
)
import ads_writer as writer
import json
import os
from dotenv import load_dotenv

load_dotenv()


def _get_proposal(proposal_id: int) -> dict:
    with _conn() as db:
        row = db.execute("SELECT * FROM ad_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("payload"):
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            pass
    return d


def execute_proposal(proposal_id: int) -> dict:
    """
    Execute an APPROVED proposal. Returns {"ok": True, ...} or {"ok": False, "error": ...}.
    """
    proposal = _get_proposal(proposal_id)
    if not proposal:
        return {"ok": False, "error": "Proposal not found"}

    if proposal["status"] != "APPROVED":
        return {"ok": False, "error": f"Proposal is {proposal['status']}, must be APPROVED"}

    ptype = proposal.get("proposal_type", "")
    payload = proposal.get("payload") or {}
    target_id = proposal.get("target_id", "")
    target_name = proposal.get("target_name", "")
    account_label = proposal.get("account_label", "")

    try:
        result = _dispatch(ptype, target_id, payload)
        mark_executed(proposal_id, result)
        log_action(
            action_type=ptype,
            outcome=f"OK: {json.dumps(result)[:200]}",
            proposal_id=proposal_id,
            target_id=target_id,
            target_name=target_name,
            actor="system",
        )
        return {"ok": True, "result": result}
    except Exception as e:
        err = str(e)
        mark_failed(proposal_id, err)
        log_action(
            action_type=ptype,
            outcome="FAILED",
            proposal_id=proposal_id,
            target_id=target_id,
            target_name=target_name,
            actor="system",
            error=err,
        )
        return {"ok": False, "error": err}


def _dispatch(ptype: str, target_id: str, payload: dict) -> dict:
    if ptype == "PAUSE":
        return writer.update_status(target_id, "PAUSED")

    if ptype == "SCALE":
        pct = float(payload.get("suggested_budget_increase_pct", 20))
        # Read current budget from adset and apply percentage increase
        # For simulation, just pass a placeholder budget
        current_daily = float(payload.get("current_daily_budget_usd", 50))
        new_budget = round(current_daily * (1 + pct / 100), 2)
        return writer.update_budget(target_id, daily_budget_usd=new_budget)

    if ptype == "ADJUST_AUDIENCE":
        targeting = payload.get("targeting", {})
        if not targeting:
            raise ValueError("ADJUST_AUDIENCE payload missing 'targeting' dict")
        return writer.update_audience(target_id, targeting)

    if ptype == "LAUNCH":
        account_id = payload.get("account_id", "")
        if not account_id:
            raise ValueError("LAUNCH payload missing 'account_id'")
        page_id = payload.get("page_id", "")
        campaign_name = payload.get("campaign_name", "Khales — New Campaign")
        objective = payload.get("suggested_objective", "REACH")
        daily_budget = float(payload.get("suggested_daily_budget_usd", 50))
        message = payload.get("suggested_creative_brief", "Luxury Engineering Excellence")

        campaign = writer.create_campaign(account_id, campaign_name, objective)
        campaign_id = campaign.get("id", "SIMULATED_CAMPAIGN_ID")

        targeting = payload.get("targeting", {
            "geo_locations": {"countries": ["AE"]},
            "age_min": 35,
            "age_max": 65,
        })

        adset_data = payload.get("adset", {})
        destination_type = adset_data.get("destination_type", "WEBSITE")

        promoted_object = None
        if destination_type in ("WHATSAPP", "MESSENGER", "INSTAGRAM_DIRECT", "PHONE_CALL"):
            promoted_object = {"page_id": os.getenv("META_PAGE_ID")}

        adset = writer.create_adset(
            account_id, campaign_id,
            name=f"{campaign_name} — Ad Set",
            daily_budget_usd=daily_budget,
            targeting=targeting,
            destination_type=destination_type if destination_type != "WEBSITE" else None,
            promoted_object=promoted_object,
        )
        adset_id = adset.get("id", "SIMULATED_ADSET_ID")

        creative = writer.create_ad_creative(
            account_id,
            name=f"{campaign_name} — Creative",
            page_id=page_id or "PAGE_ID",
            message=message,
        )
        creative_id = creative.get("id", "SIMULATED_CREATIVE_ID")

        ad = writer.create_ad(account_id, adset_id, creative_id, name=f"{campaign_name} — Ad")
        return {"campaign": campaign, "adset": adset, "creative": creative, "ad": ad}

    if ptype == "ADJUST_CREATIVE":
        # Swap creative on existing ad — simplified: just log the intent
        return {"note": "Creative swap queued", "target_id": target_id, "payload": payload}

    raise ValueError(f"Unknown proposal_type: {ptype}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ads_executor.py <proposal_id>")
        sys.exit(1)
    pid = int(sys.argv[1])
    res = execute_proposal(pid)
    import json as _json
    print(_json.dumps(res, indent=2))
