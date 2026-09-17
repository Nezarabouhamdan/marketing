import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")

# Time range: last 7 days
until = datetime.now()
since = until - timedelta(days=7)

since_ts = int(since.timestamp())
until_ts = int(until.timestamp())

print(f"📊 Insights for last 7 days")
print(f"   {since.strftime('%Y-%m-%d')} → {until.strftime('%Y-%m-%d')}\n")

# Account-level metrics (daily totals)
metrics = ["reach", "profile_views", "website_clicks", "accounts_engaged"]

r = requests.get(f"{GRAPH}/{IG_ID}/insights", params={
    "metric": ",".join(metrics),
    "period": "day",
    "metric_type": "total_value",
    "since": since_ts,
    "until": until_ts,
    "access_token": TOKEN,
})

if r.status_code != 200:
    print(f"❌ Error: {r.json()}")
    exit(1)

data = r.json().get("data", [])

print("=" * 50)
for metric in data:
    name = metric.get("name", "?")
    title = metric.get("title", name)
    value = metric.get("total_value", {}).get("value", 0)
    print(f"   {title}: {value:,}")
print("=" * 50)

# Follower count over time
print("\n👥 Follower demographics:\n")

r2 = requests.get(f"{GRAPH}/{IG_ID}/insights", params={
    "metric": "follower_count",
    "period": "day",
    "since": since_ts,
    "until": until_ts,
    "access_token": TOKEN,
})

if r2.status_code == 200:
    follower_data = r2.json().get("data", [])
    if follower_data and follower_data[0].get("values"):
        values = follower_data[0]["values"]
        total_new = sum(v.get("value", 0) for v in values)
        print(f"   New followers gained: {total_new:,}")
    else:
        print("   No follower data available for this period")
else:
    print(f"   ⚠️  Couldn't fetch follower count: {r2.json().get('error', {}).get('message', 'unknown')}")