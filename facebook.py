"""Fetch Facebook Page metrics — uses the same Page Token we already have."""
import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
PAGE_ID = os.getenv("META_PAGE_ID")


def fetch_page_info():
    r = requests.get(f"{GRAPH}/{PAGE_ID}", params={
        "fields": "name,username,fan_count,followers_count,about,category,link,picture.type(large)",
        "access_token": TOKEN,
    })
    return r.json() if r.status_code == 200 else {"error": r.json()}


def fetch_page_posts(limit=10):
    r = requests.get(f"{GRAPH}/{PAGE_ID}/posts", params={
        "fields": "id,message,created_time,permalink_url,full_picture,attachments{media_type},"
                  "reactions.summary(true).limit(0),comments.summary(true).limit(0),shares",
        "limit": limit,
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return []
    posts = r.json().get("data", [])
    # Flatten reaction/comment counts
    for p in posts:
        p["reaction_count"] = (p.get("reactions", {}).get("summary", {}) or {}).get("total_count", 0)
        p["comment_count"] = (p.get("comments", {}).get("summary", {}) or {}).get("total_count", 0)
        p["share_count"] = (p.get("shares", {}) or {}).get("count", 0)
    return posts


def fetch_page_insights(days=7):
    until = datetime.now()
    since = until - timedelta(days=days)

    r = requests.get(f"{GRAPH}/{PAGE_ID}/insights", params={
        "metric": "page_impressions,page_impressions_unique,page_post_engagements,"
                  "page_fans,page_fan_adds",
        "period": "day",
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return {"error": r.json()}

    out = {}
    for m in r.json().get("data", []):
        name = m.get("name")
        values = m.get("values", [])
        # Sum the values for the period (most metrics are daily counts)
        total = sum(v.get("value", 0) for v in values if isinstance(v.get("value"), (int, float)))
        # For page_fans, take the latest value (it's a snapshot, not a sum)
        if name == "page_fans" and values:
            total = values[-1].get("value", 0)
        out[name] = total
    return out


if __name__ == "__main__":
    print("📘 FACEBOOK PAGE OVERVIEW\n")
    info = fetch_page_info()
    if "error" in info:
        print(f"❌ {info['error']}")
    else:
        print(f"  Name: {info.get('name')}")
        print(f"  Username: @{info.get('username', '—')}")
        print(f"  Fans: {info.get('fan_count', 0):,}")
        print(f"  Followers: {info.get('followers_count', 0):,}")
        print(f"  Category: {info.get('category', '—')}\n")

    print("📊 LAST 7 DAYS INSIGHTS\n")
    ins = fetch_page_insights(days=7)
    if "error" in ins:
        print(f"❌ {ins['error']}")
    else:
        for k, v in ins.items():
            print(f"  {k}: {v:,}")

    print("\n📝 RECENT POSTS\n")
    posts = fetch_page_posts(limit=5)
    for p in posts:
        msg = (p.get("message") or "").replace("\n", " ")[:80]
        print(f"  [{p.get('created_time', '')[:10]}] ❤️ {p['reaction_count']} 💬 {p['comment_count']} 🔁 {p['share_count']}")
        print(f"    {msg}\n")
