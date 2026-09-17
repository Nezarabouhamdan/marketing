"""Meta Marketing API write operations. All guarded by ADS_WRITE_ENABLED flag."""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_USER_TOKEN") or os.getenv("META_PAGE_TOKEN")
ADS_WRITE_ENABLED = os.getenv("ADS_WRITE_ENABLED", "false").lower() == "true"


def _sim(url: str, payload: dict) -> dict:
    return {"simulated": True, "would_post_to": url, "payload": payload}


def _post(url: str, payload: dict) -> dict:
    payload["access_token"] = TOKEN
    r = requests.post(url, json=payload, timeout=30)
    data = r.json()
    if "error" in data:
        err = data["error"]
        code = err.get("code", "")
        subcode = err.get("error_subcode", "")
        msg = err.get("message", str(err))
        user_msg = err.get("error_user_msg", "")
        user_title = err.get("error_user_title", "")
        details = f"(#{code}/{subcode}) {user_title}: {user_msg} — {msg}".strip(" :—")
        raise RuntimeError(details)
    return data


def create_campaign(account_id: str, name: str, objective: str, status: str = "PAUSED") -> dict:
    url = f"{GRAPH}/act_{account_id}/campaigns"
    payload = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": [],
        "is_adset_budget_sharing_enabled": False,
    }
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def list_lead_forms(page_id: str = None) -> list:
    pid = page_id or os.getenv("META_PAGE_ID", "")
    url = f"{GRAPH}/{pid}/leadgen_forms"
    params = {"fields": "id,name,status", "access_token": TOKEN, "limit": 20}
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    return data.get("data", []) if "error" not in data else []


def list_custom_audiences(account_id: str) -> list:
    url = f"{GRAPH}/act_{account_id}/customaudiences"
    params = {
        "fields": "id,name,subtype,approximate_count_lower_bound",
        "access_token": TOKEN,
        "limit": 50,
    }
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    return data.get("data", []) if "error" not in data else []


def create_adset(
    account_id: str,
    campaign_id: str,
    name: str,
    daily_budget_usd: float,
    targeting: dict,
    optimization_goal: str = "REACH",
    billing_event: str = "IMPRESSIONS",
    status: str = "PAUSED",
    destination_type: str = None,
    promoted_object: dict = None,
) -> dict:
    url = f"{GRAPH}/act_{account_id}/adsets"
    payload = {
        "name": name,
        "campaign_id": campaign_id,
        "daily_budget": int(daily_budget_usd * 100),
        "billing_event": billing_event,
        "optimization_goal": optimization_goal,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": targeting,
        "status": status,
    }
    if destination_type:
        payload["destination_type"] = destination_type
    if promoted_object:
        payload["promoted_object"] = json.dumps(promoted_object)
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def upload_image(account_id: str, image_path: str) -> str:
    """Upload an image and return its hash."""
    url = f"{GRAPH}/act_{account_id}/adimages"
    if not ADS_WRITE_ENABLED:
        return "SIMULATED_IMAGE_HASH"
    with open(image_path, "rb") as fh:
        r = requests.post(url, files={"filename": fh}, data={"access_token": TOKEN}, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    images = data.get("images", {})
    for key in images:
        return images[key]["hash"]
    raise RuntimeError("No image hash returned")


def create_ad_creative(
    account_id: str,
    name: str,
    page_id: str,
    message: str,
    image_hash: str = "",
    link_url: str = "",
    cta_type: str = "LEARN_MORE",
    destination_type: str = "WEBSITE",
    lead_form_id: str = "",
) -> dict:
    url = f"{GRAPH}/act_{account_id}/adcreatives"
    link_data = {
        "message": message,
        "link": link_url or "https://khales.ae",
    }
    if image_hash:
        link_data["image_hash"] = image_hash

    if destination_type == "WHATSAPP":
        link_data["call_to_action"] = {
            "type": "WHATSAPP_MESSAGE",
            "value": {"app_destination": "WHATSAPP"},
        }
    elif destination_type == "LEAD_FORM" and lead_form_id:
        link_data["call_to_action"] = {
            "type": "SIGN_UP",
            "value": {"lead_gen_form_id": lead_form_id},
        }
    elif cta_type:
        cta = {"type": cta_type}
        if link_url:
            cta["value"] = {"link": link_url}
        link_data["call_to_action"] = cta

    payload = {
        "name": name,
        "object_story_spec": {
            "page_id": page_id,
            "link_data": link_data,
        },
    }
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def create_ad(account_id: str, adset_id: str, creative_id: str, name: str, status: str = "PAUSED") -> dict:
    url = f"{GRAPH}/act_{account_id}/ads"
    payload = {"name": name, "adset_id": adset_id, "creative": {"creative_id": creative_id}, "status": status}
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def update_status(object_id: str, status: str) -> dict:
    url = f"{GRAPH}/{object_id}"
    payload = {"status": status}
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def update_budget(adset_id: str, daily_budget_usd: float = None, lifetime_budget_usd: float = None) -> dict:
    url = f"{GRAPH}/{adset_id}"
    payload = {}
    if daily_budget_usd is not None:
        payload["daily_budget"] = int(daily_budget_usd * 100)
    if lifetime_budget_usd is not None:
        payload["lifetime_budget"] = int(lifetime_budget_usd * 100)
    if not payload:
        raise ValueError("Provide daily_budget_usd or lifetime_budget_usd")
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def update_audience(adset_id: str, targeting: dict) -> dict:
    url = f"{GRAPH}/{adset_id}"
    payload = {"targeting": targeting}
    if not ADS_WRITE_ENABLED:
        return _sim(url, payload)
    return _post(url, payload)


def check_spend_caps(account_id: str) -> dict:
    """Return today's spend for this account from the Insights API."""
    from datetime import date
    today = date.today().isoformat()
    url = f"{GRAPH}/act_{account_id}/insights"
    params = {
        "fields": "spend",
        "time_range": f'{{"since":"{today}","until":"{today}"}}',
        "access_token": TOKEN,
        "level": "account",
    }
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    rows = data.get("data", [])
    spend = float(rows[0].get("spend", 0)) if rows else 0.0
    return {"account_id": account_id, "today_spend_usd": spend}


