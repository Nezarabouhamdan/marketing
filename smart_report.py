import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")

claude = Anthropic()  # picks up ANTHROPIC_API_KEY from .env automatically

# ─────────────────────────────────────────────────────────────
# 1. COLLECT DATA from Instagram
# ─────────────────────────────────────────────────────────────

print("📥 Collecting data from Instagram...\n")

until = datetime.now()
since = until - timedelta(days=7)
since_ts = int(since.timestamp())
until_ts = int(until.timestamp())

# Account info
account = requests.get(f"{GRAPH}/{IG_ID}", params={
    "fields": "username,name,followers_count,follows_count,media_count",
    "access_token": TOKEN,
}).json()

# Recent posts (last 10)
posts_resp = requests.get(f"{GRAPH}/{IG_ID}/media", params={
    "fields": "caption,media_type,timestamp,like_count,comments_count,permalink",
    "limit": 10,
    "access_token": TOKEN,
}).json()
posts = posts_resp.get("data", [])

# Account insights (last 7 days)
insights_resp = requests.get(f"{GRAPH}/{IG_ID}/insights", params={
    "metric": "reach,profile_views,website_clicks,accounts_engaged",
    "period": "day",
    "metric_type": "total_value",
    "since": since_ts,
    "until": until_ts,
    "access_token": TOKEN,
}).json()
insights = {m["name"]: m.get("total_value", {}).get("value", 0)
            for m in insights_resp.get("data", [])}

# New followers
follower_resp = requests.get(f"{GRAPH}/{IG_ID}/insights", params={
    "metric": "follower_count",
    "period": "day",
    "since": since_ts,
    "until": until_ts,
    "access_token": TOKEN,
}).json()
follower_data = follower_resp.get("data", [])
new_followers = 0
if follower_data and follower_data[0].get("values"):
    new_followers = sum(v.get("value", 0) for v in follower_data[0]["values"])

print("✅ Data collected\n")

# ─────────────────────────────────────────────────────────────
# 2. FORMAT DATA for Claude
# ─────────────────────────────────────────────────────────────

# Build a clean summary of posts for Claude to analyze
posts_summary = []
for p in posts:
    caption = (p.get("caption") or "(no caption)")
    if len(caption) > 150:
        caption = caption[:150] + "..."
    posts_summary.append({
        "date": p.get("timestamp", "")[:10],
        "type": p.get("media_type"),
        "likes": p.get("like_count", 0),
        "comments": p.get("comments_count", 0),
        "caption_preview": caption,
    })

data_for_claude = f"""
ACCOUNT: @{account.get('username')} ({account.get('name')})
Total followers: {account.get('followers_count', 0):,}
Total posts: {account.get('media_count', 0):,}

LAST 7 DAYS PERFORMANCE:
- Reach: {insights.get('reach', 0):,}
- Profile views: {insights.get('profile_views', 0):,}
- Website clicks: {insights.get('website_clicks', 0):,}
- Accounts engaged: {insights.get('accounts_engaged', 0):,}
- New followers: {new_followers:,}

RECENT POSTS (last 10):
{chr(10).join(f"  • {p['date']} [{p['type']}] {p['likes']} likes, {p['comments']} comments — \"{p['caption_preview']}\"" for p in posts_summary)}
"""

# ─────────────────────────────────────────────────────────────
# 3. ASK CLAUDE to analyze and write the report
# ─────────────────────────────────────────────────────────────

print("🧠 Claude is analyzing the data...\n")

system_prompt = """You are a marketing analyst writing a weekly Instagram performance report for a business owner. 

Your report should:
- Start with a one-sentence executive summary
- Highlight the top 1-2 wins this week
- Identify the best-performing post and explain WHY it likely worked
- Flag any concerns or drops in performance
- Give 2-3 specific, actionable recommendations for next week
- Keep it concise — under 300 words total
- Use emojis sparingly for section headers
- Be honest, not overly positive

Write in a friendly but professional tone. The reader is busy and wants insight, not just numbers."""

response = claude.messages.create(
    model="claude-opus-4-5",
    max_tokens=1500,
    system=system_prompt,
    messages=[{
        "role": "user",
        "content": f"Here's this week's Instagram data. Write the weekly report.\n\n{data_for_claude}"
    }]
)

report = response.content[0].text

# ─────────────────────────────────────────────────────────────
# 4. DISPLAY THE REPORT
# ─────────────────────────────────────────────────────────────

print("=" * 60)
print(f"📊 WEEKLY INSTAGRAM REPORT — {until.strftime('%B %d, %Y')}")
print("=" * 60)
print()
print(report)
print()
print("=" * 60)

# Save to file too
filename = f"report_{until.strftime('%Y-%m-%d')}.txt"
with open(filename, "w", encoding="utf-8") as f:
    f.write(f"Weekly Instagram Report — {until.strftime('%B %d, %Y')}\n")
    f.write("=" * 60 + "\n\n")
    f.write(report)

print(f"💾 Saved to: {filename}")