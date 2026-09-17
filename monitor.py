"""Hashtag + mention monitoring via Instagram Graph API."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")
HASHTAGS = [h.strip() for h in (os.getenv("HASHTAGS", "")).split(",") if h.strip()]


def get_hashtag_id(name):
    """Look up a hashtag's ID — required before fetching posts."""
    r = requests.get(f"{GRAPH}/ig_hashtag_search", params={
        "user_id": IG_ID,
        "q": name,
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return None
    data = r.json().get("data", [])
    return data[0]["id"] if data else None


def fetch_hashtag_posts(hashtag_name, kind="top", limit=15):
    """
    Pull posts using a hashtag.
    kind = "top" (most-engaged) or "recent" (newest)
    """
    hashtag_id = get_hashtag_id(hashtag_name)
    if not hashtag_id:
        return {"hashtag": hashtag_name, "error": "hashtag not found"}

    edge = "top_media" if kind == "top" else "recent_media"
    r = requests.get(f"{GRAPH}/{hashtag_id}/{edge}", params={
        "user_id": IG_ID,
        "fields": "id,caption,media_type,permalink,timestamp,like_count,comments_count",
        "limit": limit,
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return {"hashtag": hashtag_name, "error": r.json().get("error", {}).get("message")}

    posts = r.json().get("data", [])
    return {"hashtag": hashtag_name, "id": hashtag_id, "posts": posts}


def fetch_mentions(limit=20):
    """Pull recent posts where someone tagged @khales."""
    # Mentions of the IG business account in captions
    r = requests.get(f"{GRAPH}/{IG_ID}/tags", params={
        "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
                  "like_count,comments_count,username",
        "limit": limit,
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return {"error": r.json().get("error", {}).get("message")}
    return {"mentions": r.json().get("data", [])}


def fetch_all_hashtags(kind="top", limit=10):
    """Pull top posts for every tracked hashtag."""
    results = []
    for tag in HASHTAGS:
        result = fetch_hashtag_posts(tag, kind=kind, limit=limit)
        results.append(result)
    return results


if __name__ == "__main__":
    print(f"🏷️  Tracking {len(HASHTAGS)} hashtags...\n")
    for tag in HASHTAGS:
        result = fetch_hashtag_posts(tag, kind="top", limit=5)
        print("=" * 60)
        if "error" in result:
            print(f"❌ #{tag}: {result['error']}")
            continue
        posts = result.get("posts", [])
        print(f"#{tag} — {len(posts)} top posts")
        for p in posts[:3]:
            cap = (p.get("caption") or "(no caption)").replace("\n", " ")[:80]
            print(f"  ❤️ {p.get('like_count', 0)} 💬 {p.get('comments_count', 0)} — {cap}")
        print()

    print("\n💬 MENTIONS\n")
    mentions = fetch_mentions(limit=10)
    if "error" in mentions:
        print(f"❌ {mentions['error']}")
    else:
        m_list = mentions.get("mentions", [])
        if not m_list:
            print("  (no recent mentions)")
        else:
            for m in m_list[:5]:
                cap = (m.get("caption") or "").replace("\n", " ")[:80]
                print(f"  @{m.get('username', '?')} [{m.get('timestamp', '')[:10]}] — {cap}")
