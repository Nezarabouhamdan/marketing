"""Print all action_type values from campaign insights to see what Meta returns."""
import os, requests
from dotenv import load_dotenv

load_dotenv()
GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_USER_TOKEN") or os.getenv("META_PAGE_TOKEN")

for acct in [os.getenv("META_AD_ACCOUNT_1"), os.getenv("META_AD_ACCOUNT_2")]:
    if not acct:
        continue
    print(f"\n=== {acct} ===")
    r = requests.get(f"{GRAPH}/{acct}/campaigns", params={
        "fields": "id,name,insights.date_preset(maximum){spend,actions}",
        "limit": 10,
        "access_token": TOKEN,
    }, timeout=30)
    for c in r.json().get("data", []):
        ins = (c.get("insights", {}).get("data") or [{}])[0]
        actions = ins.get("actions", [])
        if actions:
            print(f"\n  {c['name']} (${ins.get('spend','0')})")
            for a in actions:
                print(f"    {a['action_type']}: {a['value']}")
