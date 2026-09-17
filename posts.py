import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")

print("Fetching your recent posts...\n")

# Get the list of recent posts
r = requests.get(f"{GRAPH}/{IG_ID}/media", params={
    "fields": "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count",
    "limit": 10,
    "access_token": TOKEN,
})

if r.status_code != 200:
    print(f"❌ Error: {r.json()}")
    exit(1)

posts = r.json().get("data", [])

if not posts:
    print("No posts found.")
    exit(0)

print(f"Found {len(posts)} recent posts:\n")
print("=" * 70)

for i, post in enumerate(posts, 1):
    caption = (post.get("caption") or "(no caption)").replace("\n", " ")
    if len(caption) > 60:
        caption = caption[:60] + "..."
    
    likes = post.get("like_count", 0)
    comments = post.get("comments_count", 0)
    media_type = post.get("media_type", "?")
    timestamp = post.get("timestamp", "")[:10]  # just the date
    
    print(f"{i}. [{media_type}] {timestamp}")
    print(f"   {caption}")
    print(f"   ❤️  {likes:,} likes  💬 {comments:,} comments")
    print(f"   🔗 {post.get('permalink', '')}")
    print("-" * 70)