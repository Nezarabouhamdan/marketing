import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")

if not TOKEN or not IG_ID:
    print("❌ Missing META_PAGE_TOKEN or META_IG_BUSINESS_ID in .env")
    exit(1)

print("Fetching Instagram account info...\n")

r = requests.get(f"{GRAPH}/{IG_ID}", params={
    "fields": "username,name,followers_count,follows_count,media_count,biography",
    "access_token": TOKEN,
})

if r.status_code != 200:
    print(f"❌ Error: {r.json()}")
    exit(1)

data = r.json()

print("=" * 50)
print(f"📱 @{data.get('username', 'unknown')}")
print(f"   Name: {data.get('name', '—')}")
print(f"   Bio:  {data.get('biography', '—')[:60]}")
print("=" * 50)
print(f"👥 Followers:  {data.get('followers_count', 0):,}")
print(f"➡️  Following:  {data.get('follows_count', 0):,}")
print(f"📝 Posts:      {data.get('media_count', 0):,}")
print("=" * 50)