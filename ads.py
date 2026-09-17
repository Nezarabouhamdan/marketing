"""Fetch ad campaigns and performance from both ad accounts."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_USER_TOKEN")
AD_ACCOUNTS = [
    ("Account 1", os.getenv("META_AD_ACCOUNT_1")),
    ("Account 2", os.getenv("META_AD_ACCOUNT_2")),
    ("Account 3", os.getenv("META_AD_ACCOUNT_3")),
]


def fetch_campaigns(ad_account_id, limit=50):
    """Get campaigns with spend, impressions, clicks, etc."""
    r = requests.get(f"{GRAPH}/{ad_account_id}/campaigns", params={
        "fields": "id,name,objective,status,created_time,start_time,stop_time,"
                  "insights.date_preset(maximum){spend,impressions,reach,clicks,ctr,cpc,cpm,actions}",
        "limit": limit,
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return {"error": r.json()}
    return r.json().get("data", [])


def print_campaign(c):
    insights = (c.get("insights", {}).get("data") or [{}])[0]
    spend = float(insights.get("spend", 0))
    impressions = int(insights.get("impressions", 0))
    clicks = int(insights.get("clicks", 0))
    ctr = float(insights.get("ctr", 0))
    cpc = float(insights.get("cpc", 0))

    print(f"  📌 {c.get('name', 'Unnamed')}")
    print(f"     Status: {c.get('status')} | Objective: {c.get('objective')}")
    print(f"     💰 Spend: ${spend:,.2f}  👁️  Impressions: {impressions:,}")
    print(f"     🖱️  Clicks: {clicks:,}  CTR: {ctr:.2f}%  CPC: ${cpc:.2f}")
    print()


if __name__ == "__main__":
    for label, acct_id in AD_ACCOUNTS:
        if not acct_id:
            print(f"⚠️  {label}: not set in .env\n")
            continue

        print("=" * 60)
        print(f"📊 {label} ({acct_id})")
        print("=" * 60)

        campaigns = fetch_campaigns(acct_id)
        if isinstance(campaigns, dict) and "error" in campaigns:
            print(f"❌ Error: {campaigns['error']}\n")
            continue

        if not campaigns:
            print("No campaigns found.\n")
            continue

        # Skip campaigns with no spend (never launched)
        active_campaigns = []
        for c in campaigns:
            insights = (c.get("insights", {}).get("data") or [{}])[0]
            if float(insights.get("spend", 0)) > 0:
                active_campaigns.append(c)

        print(f"Found {len(campaigns)} campaigns ({len(active_campaigns)} with spend):\n")
        for c in active_campaigns:
            print_campaign(c)