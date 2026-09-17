"""Competitor tracking via Instagram business_discovery endpoint."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")
COMPETITORS = [c.strip() for c in (os.getenv("COMPETITORS", "")).split(",") if c.strip()]


def fetch_competitor(username, recent_posts=12):
    """Pull profile + recent posts for one competitor."""
    fields = (
        f"business_discovery.username({username}){{"
        f"username,name,biography,followers_count,follows_count,media_count,"
        f"profile_picture_url,website,"
        f"media.limit({recent_posts}){{id,caption,media_type,media_url,thumbnail_url,"
        f"permalink,timestamp,like_count,comments_count}}"
        f"}}"
    )
    r = requests.get(f"{GRAPH}/{IG_ID}", params={
        "fields": fields,
        "access_token": TOKEN,
    })
    if r.status_code != 200:
        return {"username": username, "error": r.json().get("error", {}).get("message", "unknown")}
    data = r.json().get("business_discovery")
    if not data:
        return {"username": username, "error": "Not found / not a business account"}
    return data


def calc_metrics(profile):
    """Calculate engagement rate and posting frequency from recent posts."""
    posts = (profile.get("media", {}) or {}).get("data", [])
    followers = profile.get("followers_count", 0) or 1

    total_likes = sum(p.get("like_count", 0) for p in posts)
    total_comments = sum(p.get("comments_count", 0) for p in posts)
    total_engagement = total_likes + total_comments

    avg_engagement_per_post = (total_engagement / len(posts)) if posts else 0
    engagement_rate = (avg_engagement_per_post / followers * 100) if followers else 0

    # Posting frequency from oldest to newest of recent posts
    if len(posts) >= 2:
        from datetime import datetime
        try:
            newest = datetime.fromisoformat(posts[0]["timestamp"].replace("Z", "+00:00"))
            oldest = datetime.fromisoformat(posts[-1]["timestamp"].replace("Z", "+00:00"))
            days = max(1, (newest - oldest).days)
            posts_per_week = round((len(posts) - 1) / days * 7, 1)
        except Exception:
            posts_per_week = None
    else:
        posts_per_week = None

    # Top post by engagement
    top_post = None
    if posts:
        top = max(posts, key=lambda p: p.get("like_count", 0) + p.get("comments_count", 0))
        top_post = {
            "caption": (top.get("caption") or "")[:120],
            "likes": top.get("like_count", 0),
            "comments": top.get("comments_count", 0),
            "permalink": top.get("permalink"),
            "thumbnail": top.get("thumbnail_url") or top.get("media_url"),
        }

    return {
        "engagement_rate": round(engagement_rate, 2),
        "avg_engagement_per_post": round(avg_engagement_per_post, 1),
        "posts_per_week": posts_per_week,
        "recent_posts_count": len(posts),
        "top_post": top_post,
    }


def fetch_all():
    results = []
    for handle in COMPETITORS:
        profile = fetch_competitor(handle)
        if "error" in profile:
            results.append({"username": handle, "error": profile["error"]})
            continue
        metrics = calc_metrics(profile)
        results.append({
            "username": profile.get("username"),
            "name": profile.get("name"),
            "biography": profile.get("biography"),
            "followers_count": profile.get("followers_count"),
            "media_count": profile.get("media_count"),
            "profile_picture_url": profile.get("profile_picture_url"),
            "website": profile.get("website"),
            **metrics,
        })
    return results


if __name__ == "__main__":
    print(f"📊 Tracking {len(COMPETITORS)} competitors...\n")
    results = fetch_all()

    for r in results:
        print("=" * 60)
        if "error" in r:
            print(f"❌ @{r['username']}: {r['error']}")
            continue
        print(f"📌 @{r['username']} — {r.get('name', '')}")
        print(f"   👥 Followers: {r.get('followers_count', 0):,}")
        print(f"   📝 Total posts: {r.get('media_count', 0):,}")
        print(f"   📈 Engagement rate: {r['engagement_rate']}%")
        print(f"   ⏱️  Posting: {r['posts_per_week']} posts/week" if r['posts_per_week'] else "   ⏱️  Posting: insufficient data")
        if r.get("top_post"):
            tp = r["top_post"]
            print(f"   🏆 Top post: ❤️ {tp['likes']} 💬 {tp['comments']}")
            print(f"      \"{tp['caption']}\"")
        print()
