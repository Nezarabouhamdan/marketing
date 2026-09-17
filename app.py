import os
import json
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from anthropic import Anthropic
from werkzeug.utils import secure_filename
from token_manager import get_token_info, TokenError, assert_token_healthy
from ads_analysis import fetch_top_ads, AD_ACCOUNTS
from facebook import fetch_page_info, fetch_page_posts, fetch_page_insights
from competitors import fetch_all as fetch_competitors
from monitor import fetch_all_hashtags, fetch_mentions
from strategist import generate_strategy, STRATEGY_FILE, strategy_is_current
from researcher import run_researcher, RESEARCHER_FILE
from bot_db import init_db
from posts_db import init_db as init_posts_db
from ads_db import (
    init_db as init_ads_db,
    get_pending_proposals, get_all_proposals,
    approve_proposal, reject_proposal,
    get_auto_rules, set_auto_rule,
    get_spend_caps, set_spend_cap,
    get_recent_actions,
)

load_dotenv()

app = Flask(__name__)
CORS(app)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

# Ensure DB tables exist on startup
init_db()
init_posts_db()
init_ads_db()
from campaign_brain import init_tables as init_brain_tables
init_brain_tables()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")
claude = Anthropic()


def fetch_account():
    r = requests.get(f"{GRAPH}/{IG_ID}", params={
        "fields": "username,name,followers_count,follows_count,media_count,profile_picture_url,biography",
        "access_token": TOKEN,
    })
    return r.json()


def fetch_posts(limit=10):
    r = requests.get(f"{GRAPH}/{IG_ID}/media", params={
        "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
        "limit": limit,
        "access_token": TOKEN,
    })
    return r.json().get("data", [])


def fetch_insights(days=7):
    until = datetime.now()
    since = until - timedelta(days=days)

    r = requests.get(f"{GRAPH}/{IG_ID}/insights", params={
        "metric": "reach,profile_views,website_clicks,accounts_engaged",
        "period": "day",
        "metric_type": "total_value",
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
        "access_token": TOKEN,
    })
    insights = {}
    for m in r.json().get("data", []):
        insights[m["name"]] = m.get("total_value", {}).get("value", 0)

    # New followers
    r2 = requests.get(f"{GRAPH}/{IG_ID}/insights", params={
        "metric": "follower_count",
        "period": "day",
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
        "access_token": TOKEN,
    })
    follower_data = r2.json().get("data", [])
    new_followers = 0
    if follower_data and follower_data[0].get("values"):
        new_followers = sum(v.get("value", 0) for v in follower_data[0]["values"])

    insights["new_followers"] = new_followers
    return insights


def get_claude_analysis(account, posts, insights, days):
    posts_summary = []
    for p in posts:
        caption = (p.get("caption") or "(no caption)")
        if len(caption) > 150:
            caption = caption[:150] + "..."
        posts_summary.append(
            f"• {p.get('timestamp', '')[:10]} [{p.get('media_type')}] "
            f"{p.get('like_count', 0)} likes, {p.get('comments_count', 0)} comments — \"{caption}\""
        )

    data_text = f"""
ACCOUNT: @{account.get('username')} ({account.get('name')})
Total followers: {account.get('followers_count', 0):,}
Total posts: {account.get('media_count', 0):,}

LAST {days} DAYS PERFORMANCE:
- Reach: {insights.get('reach', 0):,}
- Profile views: {insights.get('profile_views', 0):,}
- Website clicks: {insights.get('website_clicks', 0):,}
- Accounts engaged: {insights.get('accounts_engaged', 0):,}
- New followers: {insights.get('new_followers', 0):,}

RECENT POSTS:
{chr(10).join(posts_summary)}
"""

    system = """You are a marketing analyst writing a weekly Instagram performance report.

Format your response in clean HTML using only these tags: <h3>, <p>, <ul>, <li>, <strong>.

Structure:
<h3>📌 Executive Summary</h3>
<p>One-sentence overview</p>

<h3>🏆 Top Wins</h3>
<ul><li>...</li></ul>

<h3>⚠️ Watch Areas</h3>
<ul><li>...</li></ul>

<h3>🎯 Next Week's Recommendations</h3>
<ul><li>...</li></ul>

Be concise (under 300 words), specific, and honest."""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": data_text}],
    )
    return response.content[0].text


def analyze_ads_with_claude(account_results):
    """Have Claude analyze top ads across all accounts and find patterns."""
    if not account_results or all(r.get("error") for r in account_results):
        return "<p>No ad data to analyze.</p>"

    summary_parts = []
    for acct in account_results:
        if acct.get("error"):
            continue
        ads = acct.get("ads", [])
        if not ads:
            continue
        summary_parts.append(f"\n━━━ {acct['account']} ━━━")
        for i, ad in enumerate(ads, 1):
            ins = (ad.get("insights", {}).get("data") or [{}])[0]
            cr = ad.get("creative", {})
            spec = cr.get("object_story_spec", {})
            link_data = spec.get("link_data") or spec.get("video_data") or {}
            body = cr.get("body") or link_data.get("message") or ""
            adset = ad.get("adset", {})
            tgt = adset.get("targeting", {})
            geo = tgt.get("geo_locations", {})

            actions = ins.get("actions", [])
            actions_text = ", ".join(f"{a['action_type']}={a['value']}" for a in actions[:5])
            ds, dp = ins.get("date_start"), ins.get("date_stop")
            run_days = "?"
            if ds and dp:
                try:
                    from datetime import datetime as _dt
                    run_days = (_dt.strptime(dp, "%Y-%m-%d") - _dt.strptime(ds, "%Y-%m-%d")).days + 1
                except Exception:
                    pass

            summary_parts.append(f"""
#{i}: {ad.get('name', 'Unnamed')}
  Objective: {(ad.get('campaign') or {}).get('objective')}
  Run duration: {run_days} days ({ds} → {dp})
  Spend: ${float(ins.get('spend', 0)):.2f} | Impressions: {ins.get('impressions', 0)} | CTR: {float(ins.get('ctr', 0)):.2f}% | CPC: ${float(ins.get('cpc', 0)):.2f}
  Conversions: {actions_text or 'none'}
  Body: {body[:300]}
  Targeting: countries={geo.get('countries', [])}, cities={[c.get('name') for c in geo.get('cities', [])]}, age={tgt.get('age_min')}–{tgt.get('age_max')}, interests={[i.get('name') for i in tgt.get('interests', [])]}
  Optimization: {adset.get('optimization_goal')}
""")

    data_text = "\n".join(summary_parts)

    system = """You are a paid-media analyst for Khales, a UAE luxury engineering & construction firm targeting HNW investors.

Analyze the top-performing ads provided. Output clean HTML using only: <h3>, <p>, <ul>, <li>, <strong>.

Structure:
<h3>🏆 What's Working</h3>
<ul><li>Specific patterns you see across the winners</li></ul>

<h3>🔍 Cross-Account Insights</h3>
<p>Compare the two accounts. What's different about how each succeeds?</p>

<h3>🎯 Audience Patterns</h3>
<ul><li>What targeting is working — locations, ages, interests</li></ul>

<h3>✍️ Creative Patterns</h3>
<ul><li>Copy length, language (English vs Arabic), tone, hashtags, CTAs</li></ul>

<h3>🚀 Recommended Next Tests</h3>
<ul><li>3 specific things to try in the next campaign based on what worked</li></ul>

Be specific, reference actual ad names/numbers. Under 400 words. No fluff. If data is thin, say so honestly."""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": data_text}],
    )
    return response.content[0].text


def analyze_competitors_with_claude(competitors, our_account):
    """Compare competitors against Khales and find strategic insights."""

    # Build summary
    valid = [c for c in competitors if "error" not in c]
    if not valid:
        return "<p>No competitor data available.</p>"

    summary = f"""
KHALES (us): {our_account.get('followers_count', 0):,} followers, {our_account.get('media_count', 0)} posts

COMPETITORS:
"""
    for c in valid:
        tp = c.get("top_post") or {}
        summary += f"""
@{c['username']} ({c.get('name', '')})
  Followers: {c.get('followers_count', 0):,} | Total posts: {c.get('media_count', 0):,}
  Posts/week: {c.get('posts_per_week', '?')}
  Engagement rate: {c.get('engagement_rate', 0)}%
  Avg engagement per post: {c.get('avg_engagement_per_post', 0)}
  Top post: {tp.get('likes', 0)} likes, {tp.get('comments', 0)} comments
  Top post caption: "{(tp.get('caption') or '')[:200]}"
  Bio: {(c.get('biography') or '')[:200]}
"""

    system = """You are a competitive analyst for Khales, a UAE luxury engineering firm targeting HNW investors on Instagram.

IMPORTANT CONTEXT: In the luxury engineering/architecture niche, engagement rates are very low (0.1–1%) because HNW audiences don't like/comment publicly. Don't treat low engagement rate as inherently bad. Focus on:
- Posting consistency
- Top-post outliers (when their content hits, what was it about?)
- Visual themes
- Bio positioning
- Follower-to-output ratios

Output clean HTML using only <h3>, <p>, <ul>, <li>, <strong>, <table>, <tr>, <td>, <th>.

Structure:
<h3>📊 Competitive Position</h3>
<p>Where Khales sits relative to these competitors</p>

<h3>🏆 Who's Doing What Best</h3>
<ul><li>Specific named competitor wins</li></ul>

<h3>⚠️ Gaps in Our Strategy</h3>
<ul><li>Things competitors do that we don't</li></ul>

<h3>🎯 3 Actions for Khales</h3>
<ul><li>Specific, concrete moves based on what's working for competitors</li></ul>

Be honest, specific, reference handles by name. Under 400 words."""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": summary}],
    )
    return response.content[0].text


@app.route("/api/competitors")
def competitors_report():
    try:
        assert_token_healthy()
        our_account = fetch_account()
        competitors = fetch_competitors()
        analysis = analyze_competitors_with_claude(competitors, our_account)
        return jsonify({
            "us": our_account,
            "competitors": competitors,
            "analysis": analysis,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def analyze_monitoring_with_claude(hashtag_results, mentions):
    """Analyze hashtag landscape and mentions for opportunities."""

    valid_tags = [r for r in hashtag_results if "error" not in r]
    if not valid_tags and not mentions.get("mentions"):
        return "<p>No data to analyze.</p>"

    summary = "HASHTAG LANDSCAPE — top posts using hashtags Khales tracks:\n\n"
    for tag in valid_tags:
        posts = tag.get("posts", [])[:5]
        summary += f"#{tag['hashtag']} — top {len(posts)} posts:\n"
        for p in posts:
            cap = (p.get("caption") or "")[:200].replace("\n", " ")
            summary += f"  ❤️ {p.get('like_count', 0)} 💬 {p.get('comments_count', 0)} — \"{cap}\"\n"
        summary += "\n"

    mention_list = mentions.get("mentions", [])
    if mention_list:
        summary += f"\nMENTIONS OF @khales.ae ({len(mention_list)} recent):\n"
        for m in mention_list[:10]:
            cap = (m.get("caption") or "")[:200].replace("\n", " ")
            summary += f"  @{m.get('username', '?')} [{m.get('timestamp', '')[:10]}] — \"{cap}\"\n"

    system = """You are a social listening analyst for Khales, UAE luxury engineering firm.

Analyze the hashtag landscape and mentions. Output clean HTML using only <h3>, <p>, <ul>, <li>, <strong>.

Structure:
<h3>🏷️ Hashtag Landscape</h3>
<p>What's trending in our space — common themes, content styles winning.</p>

<h3>💡 Content Opportunities</h3>
<ul><li>Specific content angles based on what's working under these hashtags</li></ul>

<h3>💬 Mentions Summary</h3>
<p>Who's mentioning Khales and in what context. Sentiment.</p>

<h3>🎯 Actions</h3>
<ul><li>3 specific moves: hashtags to add, content to create, mentions to engage</li></ul>

Be specific, reference real captions/accounts. Under 350 words."""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": summary}],
    )
    return response.content[0].text


@app.route("/api/monitor")
def monitor_report():
    try:
        assert_token_healthy()
        hashtags = fetch_all_hashtags(kind="top", limit=10)
        mentions = fetch_mentions(limit=20)
        analysis = analyze_monitoring_with_claude(hashtags, mentions)
        return jsonify({
            "hashtags": hashtags,
            "mentions": mentions.get("mentions", []),
            "analysis": analysis,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/token_status")
def token_status():
    """Returns current token health for the dashboard banner."""
    return jsonify(get_token_info())


@app.route("/api/ads")
def ads_report():
    try:
        assert_token_healthy()
        results = []
        for label, acct_id in AD_ACCOUNTS:
            if not acct_id:
                continue
            top = fetch_top_ads(acct_id, limit=3)
            if isinstance(top, dict) and "error" in top:
                results.append({"account": label, "error": str(top["error"])})
            else:
                results.append({"account": label, "account_id": acct_id, "ads": top})

        analysis = analyze_ads_with_claude(results)
        return jsonify({"accounts": results, "analysis": analysis})
    except TokenError as e:
        return jsonify({"error": str(e), "error_type": "token"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/facebook")
def facebook_report():
    days = int(request.args.get("days", 7))
    try:
        assert_token_healthy()
        info = fetch_page_info()
        posts = fetch_page_posts(limit=10)
        insights = fetch_page_insights(days=days)

        posts_text = "\n".join(
            f"  [{p.get('created_time', '')[:10]}] ❤️ {p.get('reaction_count', 0)} "
            f"💬 {p.get('comment_count', 0)} 🔁 {p.get('share_count', 0)} — "
            f"\"{(p.get('message') or '')[:120]}\""
            for p in posts
        )
        data_text = f"""
FACEBOOK PAGE: {info.get('name')} (@{info.get('username', '—')})
Fans: {info.get('fan_count', 0):,} | Followers: {info.get('followers_count', 0):,}

LAST {days} DAYS:
- Impressions: {insights.get('page_impressions', 0):,}
- Unique reach: {insights.get('page_impressions_unique', 0):,}
- Engagements: {insights.get('page_post_engagements', 0):,}
- New fans: {insights.get('page_fan_adds', 0):,}
- Current fans: {insights.get('page_fans', 0):,}

RECENT POSTS:
{posts_text}
"""
        system = """You are a marketing analyst for Khales, a UAE luxury engineering firm.

Analyze the Facebook Page data. Output clean HTML using only: <h3>, <p>, <ul>, <li>, <strong>.

Structure:
<h3>📌 Summary</h3>
<p>One-sentence overview</p>
<h3>🏆 What's Working</h3>
<ul><li>...</li></ul>
<h3>⚠️ Concerns</h3>
<ul><li>...</li></ul>
<h3>🎯 Recommendations</h3>
<ul><li>...</li></ul>

Be honest if engagement is low. Under 300 words."""

        analysis = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": data_text}],
        ).content[0].text

        return jsonify({
            "info": info,
            "posts": posts,
            "insights": insights,
            "analysis": analysis,
            "days": days,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/report")
def report():
    days = int(request.args.get("days", 7))
    try:
        # Check token health first — fail early with clear message
        assert_token_healthy()

        account = fetch_account()
        posts = fetch_posts(limit=10)
        insights = fetch_insights(days=days)
        analysis = get_claude_analysis(account, posts, insights, days)

        return jsonify({
            "account": account,
            "posts": posts,
            "insights": insights,
            "analysis": analysis,
            "generated_at": datetime.now().isoformat(),
            "days": days,
        })
    except TokenError as e:
        return jsonify({"error": str(e), "error_type": "token"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/webhook", methods=["GET"])
def webhook_verify():
    """Meta verification handshake."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.getenv("META_WEBHOOK_VERIFY_TOKEN"):
        print("Webhook verified")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_receive():
    """Meta sends DM and comment events here."""
    from bot import handle_dm_event, handle_comment_event
    data = request.json or {}
    try:
        for entry in data.get("entry", []):
            for msg_event in entry.get("messaging", []):
                handle_dm_event(msg_event)
            for change in entry.get("changes", []):
                if change.get("field") == "comments":
                    handle_comment_event(change.get("value", {}))
    except Exception as e:
        print(f"Webhook error: {e}")
    return "OK", 200


@app.route("/api/strategy")
def strategy_view():
    """Return the latest strategy. Generate if none exists."""
    if not STRATEGY_FILE.exists():
        try:
            result = generate_strategy()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    with open(STRATEGY_FILE, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/strategy/regenerate", methods=["POST"])
def strategy_regenerate():
    """Force regenerate the strategy."""
    try:
        result = generate_strategy()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    """Serve user-uploaded files publicly so Meta can fetch them via ngrok."""
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/posts", methods=["GET"])
def list_scheduled_posts():
    from posts_db import list_posts
    status = request.args.get("status")
    return jsonify({"posts": list_posts(status=status)})


@app.route("/api/posts/upload", methods=["POST"])
def upload_media():
    if "files" not in request.files:
        return jsonify({"error": "no files field"}), 400
    saved = []
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        filename = secure_filename(f"{int(time.time())}_{f.filename}")
        path = UPLOAD_DIR / filename
        f.save(path)
        saved.append({"filename": filename, "path": str(path)})
    return jsonify({"files": saved})


@app.route("/api/posts/analyze", methods=["POST"])
def analyze_uploaded_post():
    """Send uploaded image(s) to Claude for full vision-based recommendation."""
    from smart_scheduler import analyze_and_recommend
    data = request.json or {}
    media_paths = data.get("media_paths", [])
    if not media_paths:
        return jsonify({"error": "no media_paths provided"}), 400
    full_paths = [str(UPLOAD_DIR / m) for m in media_paths]
    result = analyze_and_recommend(
        image_paths=full_paths,
        format=data.get("format", "IMAGE"),
        user_notes=data.get("notes", ""),
    )
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route("/api/posts/create", methods=["POST"])
def create_scheduled_post():
    from posts_db import create_post
    data = request.json or {}
    try:
        post_id = create_post(
            format=data["format"],
            media_paths=data["media_paths"],
            caption=data["caption"],
            hashtags=data.get("hashtags", []),
            scheduled_for=data["scheduled_for"],
            tier=data.get("tier", "general"),
        )
        return jsonify({"post_id": post_id, "status": "DRAFT"})
    except KeyError as e:
        return jsonify({"error": f"missing field: {e}"}), 400


@app.route("/api/posts/<int:post_id>", methods=["GET"])
def get_scheduled_post(post_id):
    from posts_db import get_post
    post = get_post(post_id)
    if not post:
        return jsonify({"error": "not found"}), 404
    return jsonify(post)


@app.route("/api/posts/<int:post_id>/approve", methods=["POST"])
def approve_post(post_id):
    from posts_db import update_post, get_post
    post = get_post(post_id)
    if not post:
        return jsonify({"error": "not found"}), 404
    if post["status"] != "DRAFT":
        return jsonify({"error": f"post is {post['status']}, can only approve DRAFT"}), 400
    update_post(post_id, status="SCHEDULED")
    return jsonify({"status": "SCHEDULED"})


@app.route("/api/posts/<int:post_id>/publish-now", methods=["POST"])
def publish_now(post_id):
    from publisher import publish_post
    from posts_db import get_post, update_post
    post = get_post(post_id)
    if not post:
        return jsonify({"error": "not found"}), 404
    # Allow publishing from DRAFT, SCHEDULED, or FAILED states
    if post["status"] == "DRAFT":
        update_post(post_id, status="SCHEDULED")
    result = publish_post(post_id)
    return jsonify(result)


@app.route("/api/posts/<int:post_id>/cancel", methods=["POST"])
def cancel_post(post_id):
    from posts_db import update_post, get_post
    post = get_post(post_id)
    if not post:
        return jsonify({"error": "not found"}), 404
    if post["status"] in ("PUBLISHED", "PUBLISHING"):
        return jsonify({"error": f"cannot cancel a {post['status']} post"}), 400
    update_post(post_id, status="CANCELLED")
    return jsonify({"status": "CANCELLED"})


@app.route("/api/posts/<int:post_id>/retry", methods=["POST"])
def retry_post(post_id):
    from publisher import publish_post
    from posts_db import get_post, update_post
    post = get_post(post_id)
    if not post:
        return jsonify({"error": "not found"}), 404
    if post["status"] != "FAILED":
        return jsonify({"error": "only FAILED posts can be retried"}), 400
    update_post(post_id, status="SCHEDULED", error_message=None)
    result = publish_post(post_id)
    return jsonify(result)


@app.route("/api/researcher")
def researcher_view():
    """Return latest unified researcher output. Generates if missing."""
    if not RESEARCHER_FILE.exists():
        try:
            return jsonify(run_researcher())
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    with open(RESEARCHER_FILE, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/researcher/regenerate", methods=["POST"])
def researcher_regenerate():
    try:
        return jsonify(run_researcher())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ad-manager/proposals")
def ad_manager_proposals():
    """Return pending + recent proposals and recent action log."""
    return jsonify({
        "pending": get_pending_proposals(),
        "recent": get_all_proposals(limit=30),
        "actions": get_recent_actions(limit=20),
    })


@app.route("/api/ad-manager/reject-all", methods=["POST"])
def ad_manager_reject_all():
    """Reject all currently PENDING proposals at once."""
    import sqlite3 as _sql
    try:
        db = _sql.connect("ads_manager.db")
        n = db.execute(
            "UPDATE ad_proposals SET status='REJECTED', reviewed_at=? WHERE status='PENDING'",
            (datetime.now().isoformat(),)
        ).rowcount
        db.commit()
        db.close()
        return jsonify({"rejected": n})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ad-manager/propose-now", methods=["POST"])
def ad_manager_propose_now():
    """Trigger proposal generation immediately."""
    try:
        from ads_proposer import generate_daily_proposals
        ids = generate_daily_proposals()
        return jsonify({"proposed": len(ids), "ids": ids})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ad-manager/proposal/<int:proposal_id>/approve", methods=["POST"])
def ad_manager_approve(proposal_id):
    from ads_executor import execute_proposal
    ok = approve_proposal(proposal_id)
    if not ok:
        return jsonify({"error": "Proposal not found or not PENDING"}), 400
    result = execute_proposal(proposal_id)
    return jsonify(result)


@app.route("/api/ad-manager/proposal/<int:proposal_id>/reject", methods=["POST"])
def ad_manager_reject(proposal_id):
    data = request.json or {}
    ok = reject_proposal(proposal_id, reason=data.get("reason", ""))
    if not ok:
        return jsonify({"error": "Proposal not found or not PENDING"}), 400
    return jsonify({"status": "REJECTED"})


@app.route("/api/ad-manager/settings", methods=["GET"])
def ad_manager_settings_get():
    return jsonify({
        "auto_rules": get_auto_rules(),
        "spend_caps": get_spend_caps(),
        "write_enabled": os.getenv("ADS_WRITE_ENABLED", "false").lower() == "true",
    })


@app.route("/api/ad-manager/settings", methods=["POST"])
def ad_manager_settings_post():
    data = request.json or {}
    for rule in data.get("auto_rules", []):
        set_auto_rule(
            rule["rule_key"],
            enabled=rule.get("enabled", False),
            threshold=rule.get("threshold"),
        )
    for cap in data.get("spend_caps", []):
        set_spend_cap(cap["cap_key"], amount_usd=float(cap["amount_usd"]))
    return jsonify({"saved": True})


from scheduler import start_scheduler, load_cache, daily_refresh

# Start scheduler in background when app starts
scheduler = start_scheduler()

# Endpoint to manually trigger a refresh (useful for testing)
@app.route("/api/refresh-now", methods=["POST"])
def refresh_now():
    daily_refresh()
    return jsonify({"status": "refreshed", "at": datetime.now().isoformat()})


# Endpoint to check what's in the cache
@app.route("/api/cache-status")
def cache_status():
    cache = load_cache()
    return jsonify({
        "refreshed_at": cache.get("refreshed_at"),
        "available_sections": [k for k in cache.keys() if k not in ("refreshed_at", "errors")],
        "errors": cache.get("errors", []),
    })


@app.route("/api/control-room")
def control_room():
    import sqlite3 as _sql
    from scheduler import load_cache, LOG_FILE

    cache = load_cache()

    # Refresh status
    sections = [k for k in cache.keys() if k not in ("refreshed_at", "errors")]
    refresh = {
        "last_at": cache.get("refreshed_at"),
        "sections": sections,
        "errors": cache.get("errors", []),
    }

    # Ad proposals breakdown
    proposals = {"pending_total": 0, "by_type": {}, "oldest_pending_days": 0}
    try:
        db = _sql.connect("ads_manager.db")
        db.row_factory = _sql.Row
        rows = db.execute(
            "SELECT proposal_type, COUNT(*) as cnt FROM ad_proposals WHERE status='PENDING' GROUP BY proposal_type"
        ).fetchall()
        proposals["by_type"] = {r["proposal_type"]: r["cnt"] for r in rows}
        proposals["pending_total"] = sum(proposals["by_type"].values())
        oldest = db.execute(
            "SELECT MIN(created_at) as oldest FROM ad_proposals WHERE status='PENDING'"
        ).fetchone()
        if oldest and oldest["oldest"]:
            from datetime import timezone
            delta = datetime.now() - datetime.fromisoformat(oldest["oldest"])
            proposals["oldest_pending_days"] = delta.days
        db.close()
    except Exception:
        pass

    # Posts summary
    posts = {"scheduled": 0, "draft": 0, "failed": 0, "due_today": 0}
    try:
        db = _sql.connect("scheduled_posts.db")
        db.row_factory = _sql.Row
        for row in db.execute("SELECT status, COUNT(*) as cnt FROM scheduled_posts GROUP BY status").fetchall():
            s = row["status"].lower()
            if s in posts:
                posts[s] = row["cnt"]
        today_end = datetime.now().strftime("%Y-%m-%d") + "T23:59:59"
        today_start = datetime.now().strftime("%Y-%m-%d") + "T00:00:00"
        posts["due_today"] = db.execute(
            "SELECT COUNT(*) FROM scheduled_posts WHERE status='SCHEDULED' AND scheduled_for BETWEEN ? AND ?",
            (today_start, today_end)
        ).fetchone()[0]
        db.close()
    except Exception:
        pass

    # Strategy bullets — extract plain text lines from strategy HTML
    strategy_bullets = []
    try:
        strat = cache.get("strategy", {})
        html = strat.get("strategy_html", "") if isinstance(strat, dict) else ""
        if not html:
            import json as _json
            sf = Path("latest_strategy.json")
            if sf.exists():
                data = _json.loads(sf.read_text(encoding="utf-8"))
                html = data.get("strategy_html", "")
        if html:
            import re
            items = re.findall(r"<li[^>]*>(.*?)</li>", html, re.DOTALL)
            strategy_bullets = [re.sub(r"<[^>]+>", "", li).strip() for li in items[:8] if li.strip()]
    except Exception:
        pass

    # Scheduler log tail
    log_tail = []
    try:
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
            log_tail = lines[-20:]
    except Exception:
        pass

    return jsonify({
        "refresh": refresh,
        "proposals": proposals,
        "posts": posts,
        "strategy_bullets": strategy_bullets,
        "log_tail": log_tail,
    })


@app.route("/api/manager")
def manager_dashboard():
    import sqlite3 as _sql
    import re
    from datetime import timedelta

    now = datetime.now()
    # Week runs Mon–Sun
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    week_start_str = week_start.strftime("%Y-%m-%d")

    DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    FORMAT_MAP = {"carousel": "CAROUSEL", "reel": "REELS", "single image": "IMAGE", "image": "IMAGE", "video": "VIDEO"}

    # ── 1. Parse strategy tasks ──────────────────────────────────────────────
    strategy_tasks = []
    strategy_generated_at = None
    try:
        sf = Path("latest_strategy.json")
        if sf.exists():
            strat = json.loads(sf.read_text(encoding="utf-8"))
            strategy_generated_at = strat.get("generated_at")
            html = strat.get("strategy_html", "")
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
            for row in rows[1:]:  # skip header
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                if len(cells) >= 4 and cells[0].strip():
                    day_key = cells[0].strip()[:3].lower()
                    day_num = DAY_MAP.get(day_key)
                    target_date = (week_start + timedelta(days=day_num)).strftime("%Y-%m-%d") if day_num is not None else None
                    fmt_raw = cells[1].strip().lower()
                    fmt_norm = next((v for k, v in FORMAT_MAP.items() if k in fmt_raw), "IMAGE")
                    strategy_tasks.append({
                        "day": cells[0],
                        "target_date": target_date,
                        "format": fmt_norm,
                        "format_raw": cells[1],
                        "topic": cells[2],
                        "tier": cells[3].lower(),
                        "caption_dir": cells[4] if len(cells) > 4 else "",
                        "hashtags": cells[5] if len(cells) > 5 else "",
                        "matched_post": None,
                        "status": "pending",
                    })
    except Exception:
        pass

    # ── 2. Posts this week ───────────────────────────────────────────────────
    posts_this_week = []
    posts_by_status = {}
    try:
        db = _sql.connect("scheduled_posts.db")
        db.row_factory = _sql.Row
        rows = db.execute(
            "SELECT id, format, tier, status, scheduled_for, caption, created_at, ig_permalink "
            "FROM scheduled_posts WHERE DATE(scheduled_for) >= ? ORDER BY scheduled_for",
            (week_start_str,)
        ).fetchall()
        posts_this_week = [dict(r) for r in rows]
        for p in posts_this_week:
            s = p["status"]
            posts_by_status[s] = posts_by_status.get(s, 0) + 1
        db.close()
    except Exception:
        pass

    # ── 3. Strategy compliance: match tasks → posts ──────────────────────────
    used_post_ids = set()
    for task in strategy_tasks:
        for post in posts_this_week:
            if post["id"] in used_post_ids:
                continue
            post_date = (post.get("scheduled_for") or "")[:10]
            tier_match = post.get("tier", "").lower() == task["tier"]
            format_match = post.get("format", "").upper() == task["format"]
            date_match = post_date == task.get("target_date")
            active = post["status"] not in ("CANCELLED",)
            if active and (date_match or (tier_match and format_match)):
                task["matched_post"] = {
                    "id": post["id"],
                    "status": post["status"],
                    "scheduled_for": post["scheduled_for"],
                    "caption_preview": (post.get("caption") or "")[:80],
                    "permalink": post.get("ig_permalink"),
                }
                task["status"] = "done" if post["status"] in ("PUBLISHED", "SCHEDULED", "PUBLISHING") else "draft"
                used_post_ids.add(post["id"])
                break

    done = sum(1 for t in strategy_tasks if t["status"] in ("done", "draft"))
    compliance_pct = round(done / len(strategy_tasks) * 100) if strategy_tasks else None

    # ── 4. Ad proposer activity this week ────────────────────────────────────
    ad_activity = {}
    try:
        db = _sql.connect("ads_manager.db")
        db.row_factory = _sql.Row
        for row in db.execute(
            "SELECT status, COUNT(*) as cnt FROM ad_proposals WHERE DATE(created_at) >= ? GROUP BY status",
            (week_start_str,)
        ).fetchall():
            ad_activity[row["status"]] = row["cnt"]
        ad_activity["pending_total"] = db.execute(
            "SELECT COUNT(*) FROM ad_proposals WHERE status='PENDING'"
        ).fetchone()[0]
        ad_activity["executed_total"] = db.execute(
            "SELECT COUNT(*) FROM ad_actions_log WHERE DATE(logged_at) >= ?", (week_start_str,)
        ).fetchone()[0]
        db.close()
    except Exception:
        pass

    # ── 5. Scheduler log summary ──────────────────────────────────────────────
    log_tail = []
    try:
        lf = Path("scheduler.log")
        if lf.exists():
            log_tail = lf.read_text(encoding="utf-8").splitlines()[-15:]
    except Exception:
        pass

    cache = load_cache()

    return jsonify({
        "week_start": week_start_str,
        "week_end": week_end.strftime("%Y-%m-%d"),
        "strategy_generated_at": strategy_generated_at,
        "strategy_tasks": strategy_tasks,
        "compliance_pct": compliance_pct,
        "compliance_done": done,
        "compliance_total": len(strategy_tasks),
        "posts_this_week": posts_this_week,
        "posts_by_status": posts_by_status,
        "ad_activity": ad_activity,
        "cache_sections": list(cache.keys()),
        "last_refresh": cache.get("refreshed_at"),
        "log_tail": log_tail,
    })


@app.route("/api/ad-manager/ai-suggest-ad", methods=["POST"])
def ad_manager_ai_suggest():
    """
    Claude analyzes all available data + optional image and returns a fully filled ad spec.
    Accepts multipart/form-data: account_id, image (optional).
    """
    import base64
    import re

    account_id = request.form.get("account_id", "")
    cache = load_cache()

    # ── Build intelligence context ────────────────────────────────────────────
    ctx_parts = []

    # interest_id -> {name, ctr_sum, cpc_sum, count} for performance ranking
    interest_perf = {}

    ads = cache.get("ads", {})
    if ads.get("accounts"):
        lines = ["TOP PERFORMING ADS (recent):"]
        for acct in ads["accounts"]:
            if acct.get("error"):
                continue
            for ad in acct.get("ads", [])[:5]:
                ins = (ad.get("insights", {}).get("data") or [{}])[0]
                cr = ad.get("creative", {})
                spec = cr.get("object_story_spec", {}) or {}
                ld = spec.get("link_data") or spec.get("video_data") or {}
                body = (cr.get("body") or ld.get("message") or "")[:300]
                adset = ad.get("adset", {}) or {}
                tgt = adset.get("targeting", {}) or {}
                geo = tgt.get("geo_locations", {}) or {}
                dest = adset.get("destination_type") or "WEBSITE"
                cta = (ld.get("call_to_action") or {}).get("type", "LEARN_MORE")
                ctr = float(ins.get("ctr", 0))
                cpc = float(ins.get("cpc", 0))
                # Collect interests with performance data
                ad_interests = []
                for i in tgt.get("interests", []):
                    ad_interests.append(i)
                for spec_item in tgt.get("flexible_spec", []):
                    for i in spec_item.get("interests", []):
                        ad_interests.append(i)
                for i in ad_interests:
                    iid = i["id"]
                    if iid not in interest_perf:
                        interest_perf[iid] = {"name": i.get("name", ""), "ctr_sum": 0, "cpc_sum": 0, "count": 0}
                    interest_perf[iid]["ctr_sum"] += ctr
                    interest_perf[iid]["cpc_sum"] += cpc
                    interest_perf[iid]["count"] += 1
                lines.append(
                    f"  [{(ad.get('campaign') or {}).get('objective')}] {ad.get('name', '')}\n"
                    f"  Spend: ${float(ins.get('spend', 0)):.0f} | CTR: {ctr:.2f}% | "
                    f"CPC: ${cpc:.2f} | CPM: ${float(ins.get('cpm', 0)):.2f} | Dest: {dest} | CTA: {cta}\n"
                    f"  Countries: {geo.get('countries', [])} | Age: {tgt.get('age_min')}-{tgt.get('age_max')}\n"
                    f"  Interests: {[i.get('name') for i in ad_interests[:6]]}"
                )
        ctx_parts.append("\n".join(lines))

    # Build ranked interests list with avg CTR/CPC
    if interest_perf:
        ranked = sorted(
            interest_perf.items(),
            key=lambda x: x[1]["ctr_sum"] / max(x[1]["count"], 1),
            reverse=True
        )
        interests_list = [
            {
                "id": iid,
                "name": v["name"],
                "avg_ctr": round(v["ctr_sum"] / v["count"], 2),
                "avg_cpc": round(v["cpc_sum"] / v["count"], 2) if v["cpc_sum"] > 0 else None,
            }
            for iid, v in ranked
        ]
        ctx_parts.append(
            f"AVAILABLE INTERESTS (ranked by avg CTR — use top performers):\n"
            f"{json.dumps(interests_list)}"
        )

    # Campaign memory learnings (WINNER/UNDERPERFORMER verdicts)
    try:
        from campaign_brain import get_campaign_history, get_agent_memories
        campaigns = get_campaign_history(limit=20)
        memories = get_agent_memories(limit=10)
        if campaigns:
            lines = ["PAST CAMPAIGN PERFORMANCE (from campaign memory):"]
            for c in campaigns:
                if c.get("ai_verdict"):
                    lines.append(
                        f"  [{c['ai_verdict']}] {c.get('name', '')} | {c.get('objective', '')} | "
                        f"${c.get('spend_usd', 0):.0f} spend | CTR: {c.get('ctr', 0):.2f}% | "
                        f"Leads: {c.get('leads', 0)} | Messages: {c.get('messages', 0)}"
                    )
                    if c.get("ai_notes"):
                        lines.append(f"    → {c['ai_notes']}")
            ctx_parts.append("\n".join(lines))
        if memories:
            ctx_parts.append(
                "AGENT LEARNINGS FROM PAST CAMPAIGNS:\n" +
                "\n".join(f"  • {m['content']}" for m in memories)
            )
    except Exception:
        pass

    # Custom audiences for this account
    custom_audiences = []
    try:
        import ads_writer as _aw
        raw_id = account_id.replace("act_", "")
        custom_audiences = _aw.list_custom_audiences(raw_id or account_id)
        if custom_audiences:
            aud_lines = ["AVAILABLE CUSTOM AUDIENCES — pick the best fit for this campaign's funnel stage:"]
            for a in custom_audiences:
                subtype = a.get("subtype", "CUSTOM")
                size = int(a.get("approximate_count_lower_bound", 0))
                use_case = {
                    "WEBSITE": "retargeting — warm, high intent",
                    "ENGAGEMENT": "retargeting — warm, saw your content",
                    "LOOKALIKE": "prospecting — similar to your best customers",
                    "CUSTOM": "direct — uploaded contact list",
                    "OFFLINE_CONVERSION": "offline buyers — very warm",
                }.get(subtype, "custom audience")
                aud_lines.append(
                    f"  id={a['id']} | {a['name']} | subtype={subtype} | size≈{size:,} | best_for={use_case}"
                )
            ctx_parts.append("\n".join(aud_lines))
    except Exception:
        pass

    # Current strategy
    try:
        sf = Path("latest_strategy.json")
        if sf.exists():
            strat = json.loads(sf.read_text(encoding="utf-8"))
            html = strat.get("strategy_html", "")
            plain = re.sub(r"<[^>]+>", " ", html).strip()[:2000]
            ctx_parts.append(f"CURRENT WEEKLY STRATEGY:\n{plain}")
    except Exception:
        pass

    # Researcher brief
    researcher = cache.get("researcher", {})
    if researcher.get("brief_html"):
        plain = re.sub(r"<[^>]+>", " ", researcher["brief_html"]).strip()[:1500]
        ctx_parts.append(f"RESEARCHER INTELLIGENCE BRIEF:\n{plain}")

    # Competitors
    comp = cache.get("competitors", {})
    if comp.get("competitors"):
        lines = ["COMPETITOR LANDSCAPE:"]
        for c in comp["competitors"]:
            if c.get("error"):
                continue
            tp = c.get("top_post") or {}
            lines.append(
                f"  @{c.get('username')}: {c.get('followers_count', 0):,} followers, "
                f"{c.get('engagement_rate', 0)}% ER | "
                f"Top post: {tp.get('likes', 0)} likes — \"{(tp.get('caption') or '')[:100]}\""
            )
        ctx_parts.append("\n".join(lines))

    full_context = "\n\n".join(ctx_parts)

    system = """You are a senior paid media strategist for Khales, a UAE luxury engineering & construction firm targeting HNW investors aged 35-65.

You receive full campaign performance data, agent learnings, ranked interests (by avg CTR), custom audiences with subtypes, and optionally an ad image.

AUDIENCE SUBTYPE GUIDE:
- WEBSITE / ENGAGEMENT retargeting → best for LEADS / WHATSAPP (warm audience)
- LOOKALIKE → best for AWARENESS / TRAFFIC (similar to best customers)
- CUSTOM (email/phone) → best for direct conversion campaigns
- When no custom audience fits, use null and target with interests only (cold audience)

FUNNEL LOGIC:
- If recent WINNER campaigns used WhatsApp → recommend WhatsApp next
- If spend is high but leads/messages are 0 → recommend awareness first, then retarget
- If there are WEBSITE/ENGAGEMENT audiences → prioritize those for conversion campaigns
- If there are LOOKALIKE audiences → use for scaling WINNER campaigns

Return ONLY a valid JSON object — no markdown, no text outside JSON:
{
  "destination_type": "WEBSITE" | "WHATSAPP" | "LEAD_FORM",
  "campaign_name": "short descriptive name under 60 chars",
  "objective": "OUTCOME_AWARENESS" | "OUTCOME_TRAFFIC" | "OUTCOME_LEADS" | "OUTCOME_ENGAGEMENT" | "OUTCOME_SALES",
  "daily_budget_usd": number,
  "countries": ["AE"] or ["AE","SA","QA","KW","BH"] based on what performed in past campaigns,
  "age_min": number,
  "age_max": number,
  "gender": "ALL" | "MALE" | "FEMALE",
  "device": "ALL" | "MOBILE" | "DESKTOP",
  "languages": ["AR"] or ["EN"] or ["AR","EN"] or [],
  "interests": [{"id":"...","name":"..."}] — TOP 3-6 from AVAILABLE INTERESTS by avg_ctr. Empty array only when using a custom audience that makes interests redundant.,
  "recommended_audience_id": "id from AVAILABLE CUSTOM AUDIENCES or null",
  "recommended_audience_name": "name or null",
  "recommended_audience_subtype": "WEBSITE|LOOKALIKE|CUSTOM|ENGAGEMENT or null",
  "placement": "automatic" | "feed" | "stories" | "reels" | "all_instagram",
  "cta_type": "LEARN_MORE" | "CONTACT_US" | "GET_QUOTE" | "SEND_MESSAGE" | "SIGN_UP",
  "copy": "compelling ad copy — luxury tone, Arabic preferred for warm/retargeting audiences, English for cold/lookalike, under 200 chars",
  "link": "https://khales.ae",
  "reasoning": "4-5 sentences: (1) funnel stage and why this objective, (2) which interests and why (reference CTR numbers), (3) which audience and why (reference subtype and size), (4) gender/device/language rationale, (5) budget rationale"
}

Rules:
- gender: Use MALE if targeting investors/decision-makers and past winning campaigns skew male. Use ALL by default.
- device: Use MOBILE for WhatsApp/engagement campaigns (UAE HNW use mobile). Use ALL for awareness.
- languages: ["AR"] for retargeting UAE nationals. ["EN"] for expat HNW. ["AR","EN"] for broad. [] for advantage audience.
- interests: ONLY IDs from the AVAILABLE INTERESTS list. Empty array is valid when audience is a strong custom/lookalike.
- Budget: $50-100 for cold/test, $100-200 for warm audiences, $200+ for WINNER campaign scale.
- Copy: If image provided, tailor copy to what you see. Arabic copy for retargeting, English for cold awareness."""

    # Build message content
    content = []
    image_file = request.files.get("image")
    if image_file and image_file.filename:
        raw = image_file.read()
        img_b64 = base64.standard_b64encode(raw).decode("utf-8")
        media_type = image_file.content_type or "image/jpeg"
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": img_b64},
        })

    content.append({"type": "text", "text": full_context or "No cached data available yet — use best judgment for Khales UAE luxury brand."})

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        raw_text = response.content[0].text.strip()
        # Strip markdown code fences if present
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)
        suggestion = json.loads(raw_text)
        return jsonify(suggestion)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Claude returned invalid JSON: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ad-manager/campaign-history")
def ad_manager_campaign_history():
    from campaign_brain import get_campaign_history, get_agent_memories, get_next_step_recommendation
    return jsonify({
        "campaigns": get_campaign_history(limit=30),
        "memories":  get_agent_memories(limit=15),
        "next_step": get_next_step_recommendation(),
    })


@app.route("/api/ad-manager/sync-campaigns", methods=["POST"])
def ad_manager_sync_campaigns():
    from campaign_brain import sync_campaigns, analyze_and_learn
    from ads_analysis import AD_ACCOUNTS
    synced   = sync_campaigns(AD_ACCOUNTS)
    analyzed = analyze_and_learn(limit=10)
    return jsonify({"synced": synced, "analyzed": analyzed})


@app.route("/api/ad-manager/lead-forms")
def ad_manager_lead_forms():
    import ads_writer
    return jsonify({"forms": ads_writer.list_lead_forms()})


@app.route("/api/ad-manager/audiences")
def ad_manager_audiences():
    import ads_writer
    account_id = request.args.get("account_id", "")
    if not account_id:
        return jsonify({"audiences": []})
    return jsonify({"audiences": ads_writer.list_custom_audiences(account_id)})


@app.route("/api/ad-manager/upload-image", methods=["POST"])
def ad_manager_upload_image():
    """Upload an image for an ad creative. Returns image_hash (or simulated hash)."""
    import ads_writer
    import tempfile

    account_id = request.form.get("account_id", "")
    if not account_id:
        return jsonify({"error": "account_id required"}), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file required"}), 400

    suffix = Path(secure_filename(f.filename)).suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        image_hash = ads_writer.upload_image(account_id, tmp_path)
        return jsonify({"hash": image_hash, "simulated": not ads_writer.ADS_WRITE_ENABLED})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route("/api/ad-manager/create-ad", methods=["POST"])
def ad_manager_create_ad():
    """
    Create a full campaign → adset → creative → ad pipeline.
    Body: {account_id, campaign_name, objective, daily_budget_usd,
           status, countries, age_min, age_max, copy, link, image_hash}
    """
    import ads_writer

    data = request.json or {}
    account_id = data.get("account_id", "")
    campaign_name = data.get("campaign_name", "New Campaign")
    objective = data.get("objective", "OUTCOME_AWARENESS")
    daily_budget_usd = float(data.get("daily_budget_usd", 10))
    status = data.get("status", "PAUSED")
    countries = data.get("countries") or ["AE"]
    age_min = int(data.get("age_min", 25))
    age_max = int(data.get("age_max", 55))
    copy = data.get("copy", "")
    link = data.get("link", "")
    image_hash = data.get("image_hash", "")
    page_id = os.getenv("META_PAGE_ID", "")

    if not account_id:
        return jsonify({"error": "account_id required"}), 400

    destination_type = data.get("destination_type", "WEBSITE")
    cta_type = data.get("cta_type", "LEARN_MORE")
    lead_form_id = data.get("lead_form_id", "")
    interest_ids = data.get("interest_ids", [])       # [{id, name}]
    custom_audience_ids = data.get("custom_audience_ids", [])
    placement = data.get("placement", "automatic")
    gender = data.get("gender", "ALL")         # ALL | MALE | FEMALE
    device = data.get("device", "ALL")         # ALL | MOBILE | DESKTOP
    languages = data.get("languages", [])      # ["AR"] | ["EN"] | []

    # Map objective → optimization_goal + billing_event
    OBJECTIVE_SETTINGS = {
        "OUTCOME_AWARENESS":      ("REACH", "IMPRESSIONS"),
        "OUTCOME_TRAFFIC":        ("LINK_CLICKS", "LINK_CLICKS"),
        "OUTCOME_ENGAGEMENT":     ("POST_ENGAGEMENT", "IMPRESSIONS"),
        "OUTCOME_LEADS":          ("LEAD_GENERATION", "IMPRESSIONS"),
        "OUTCOME_SALES":          ("OFFSITE_CONVERSIONS", "IMPRESSIONS"),
        "OUTCOME_APP_PROMOTION":  ("APP_INSTALLS", "IMPRESSIONS"),
    }
    optimization_goal, billing_event = OBJECTIVE_SETTINGS.get(objective, ("REACH", "IMPRESSIONS"))
    # WhatsApp destination overrides optimization goal
    if destination_type == "WHATSAPP":
        optimization_goal = "CONVERSATIONS"
        billing_event = "IMPRESSIONS"

    # Placement → targeting additions
    PLACEMENT_MAP = {
        "automatic":     {},
        "feed":          {"publisher_platforms": ["facebook", "instagram"], "facebook_positions": ["feed"], "instagram_positions": ["stream"]},
        "stories":       {"publisher_platforms": ["facebook", "instagram"], "facebook_positions": ["story"], "instagram_positions": ["story"]},
        "reels":         {"publisher_platforms": ["instagram", "facebook"], "facebook_positions": ["reels"], "instagram_positions": ["reels"]},
        "all_instagram": {"publisher_platforms": ["instagram"], "instagram_positions": ["stream", "story", "reels", "explore"]},
    }

    targeting = {
        "geo_locations": {"countries": countries if isinstance(countries, list) else [countries]},
        "age_min": age_min,
        "age_max": age_max,
        "targeting_automation": {"advantage_audience": 0},
    }
    targeting.update(PLACEMENT_MAP.get(placement, {}))
    if interest_ids:
        targeting["flexible_spec"] = [{"interests": interest_ids}]
    if custom_audience_ids:
        targeting["custom_audiences"] = [{"id": aid} for aid in custom_audience_ids]
    # Gender: 1=Male, 2=Female (omit for all)
    if gender == "MALE":
        targeting["genders"] = [1]
    elif gender == "FEMALE":
        targeting["genders"] = [2]
    # Device
    if device == "MOBILE":
        targeting["device_platforms"] = ["mobile"]
    elif device == "DESKTOP":
        targeting["device_platforms"] = ["desktop"]
    # Language
    if languages:
        targeting["locales"] = [23 if l == "AR" else 6 for l in languages if l in ("AR", "EN")]

    step = "campaign"
    try:
        # Step 1 — Campaign
        camp = ads_writer.create_campaign(account_id, campaign_name, objective, status)
        campaign_id = camp.get("id", "SIMULATED_CAMPAIGN_ID")

        # Step 2 — Ad Set
        step = "adset"
        adset_name = f"{campaign_name} — Ad Set"
        promoted_object = None
        if destination_type in ("WHATSAPP", "MESSENGER", "INSTAGRAM_DIRECT", "PHONE_CALL"):
            promoted_object = {"page_id": page_id}
        adset = ads_writer.create_adset(
            account_id, campaign_id, adset_name,
            daily_budget_usd=daily_budget_usd,
            targeting=targeting,
            optimization_goal=optimization_goal,
            billing_event=billing_event,
            status=status,
            destination_type=destination_type if destination_type != "WEBSITE" else None,
            promoted_object=promoted_object,
        )
        adset_id = adset.get("id", "SIMULATED_ADSET_ID")

        # Step 3 — Creative
        step = "creative"
        creative_name = f"{campaign_name} — Creative"
        creative = ads_writer.create_ad_creative(
            account_id, creative_name, page_id,
            message=copy,
            image_hash=image_hash,
            link_url=link,
            cta_type=cta_type,
            destination_type=destination_type,
            lead_form_id=lead_form_id,
        )
        creative_id = creative.get("id", "SIMULATED_CREATIVE_ID")

        # Step 4 — Ad
        step = "ad"
        ad = ads_writer.create_ad(account_id, adset_id, creative_id, campaign_name, status)
        ad_id = ad.get("id", "SIMULATED_AD_ID")

        simulated = not ads_writer.ADS_WRITE_ENABLED
        return jsonify({
            "simulated": simulated,
            "campaign_id": campaign_id,
            "adset_id": adset_id,
            "creative_id": creative_id,
            "ad_id": ad_id,
            "status": status,
            "message": (
                "Ad created in simulation mode — set ADS_WRITE_ENABLED=true to go live"
                if simulated else "Ad created successfully"
            ),
        })
    except Exception as e:
        return jsonify({"error": f"[{step}] {e}"}), 500


# ── Live Campaign Tracking & Controls ────────────────────────────────────────

@app.route("/api/campaigns/live")
def campaigns_live():
    """
    Fetch all ACTIVE campaigns across both accounts with today's metrics
    and adset budget info for budget controls.
    """
    import ads_writer
    from ads_analysis import AD_ACCOUNTS

    def _extract_results(actions, wa_types, lead_types):
        wa = sum(float(a["value"]) for a in actions if a.get("action_type") in wa_types)
        leads = sum(float(a["value"]) for a in actions if a.get("action_type") in lead_types)
        return int(wa), int(leads)

    WA_TYPES = {
        "onsite_conversion.messaging_conversation_started_7d",
        "onsite_conversion.total_messaging_connection",
    }
    LEAD_TYPES = {
        "lead", "onsite_conversion.lead_grouped",
        "offsite_complete_registration_add_meta_leads",
    }

    result = []
    for label, account_id in AD_ACCOUNTS:
        if not account_id:
            continue
        raw_id = account_id.replace("act_", "")
        try:
            r = requests.get(f"{GRAPH}/{account_id}/campaigns", params={
                "fields": (
                    "id,name,objective,status,effective_status,created_time,"
                    "adsets{"
                        "id,name,daily_budget,lifetime_budget,status,effective_status,"
                        "targeting,optimization_goal,billing_event,start_time,end_time,"
                        "insights.date_preset(today){spend,impressions,reach,clicks,ctr,frequency,actions}"
                    "},"
                    "insights.date_preset(today){spend,impressions,reach,clicks,ctr,cpc,cpm,frequency,actions}"
                ),
                "filtering": json.dumps([{
                    "field": "effective_status",
                    "operator": "IN",
                    "value": ["ACTIVE", "CAMPAIGN_PAUSED", "ADSET_PAUSED"],
                }]),
                "limit": 50,
                "access_token": TOKEN,
            }, timeout=45)
            data = r.json()
            if "error" in data:
                result.append({"account": label, "error": data["error"].get("message", str(data["error"]))})
                continue

            # Also fetch lifetime totals per campaign in a single batch
            campaign_ids = [c["id"] for c in data.get("data", [])]
            lifetime_map = {}
            if campaign_ids:
                batch_payload = [
                    {
                        "method": "GET",
                        "relative_url": (
                            f"{cid}/insights?date_preset=maximum"
                            "&fields=spend,impressions,reach,clicks,ctr,cpc,cpm,frequency,actions"
                            f"&access_token={TOKEN}"
                        ),
                    }
                    for cid in campaign_ids[:25]  # batch API limit
                ]
                try:
                    br = requests.post(
                        "https://graph.facebook.com/v21.0",
                        params={"access_token": TOKEN},
                        json={"batch": batch_payload},
                        timeout=30,
                    )
                    for cid, item in zip(campaign_ids[:25], br.json()):
                        if item and item.get("code") == 200:
                            body = json.loads(item["body"])
                            lifetime_map[cid] = (body.get("data") or [{}])[0]
                except Exception:
                    pass  # lifetime stats are bonus — don't crash if batch fails

            for c in data.get("data", []):
                ins_today = (c.get("insights", {}).get("data") or [{}])[0]
                ins_life  = lifetime_map.get(c["id"], {})
                adsets    = c.get("adsets", {}).get("data") or []

                total_daily = sum(int(a.get("daily_budget", 0)) for a in adsets if a.get("daily_budget"))
                budget_usd  = total_daily / 100

                actions_today = ins_today.get("actions") or []
                wa_today, leads_today = _extract_results(actions_today, WA_TYPES, LEAD_TYPES)

                actions_life  = ins_life.get("actions") or []
                wa_life, leads_life = _extract_results(actions_life, WA_TYPES, LEAD_TYPES)

                spend_today = float(ins_today.get("spend", 0))
                spend_life  = float(ins_life.get("spend", 0))
                pct_spent   = round((spend_today / budget_usd * 100) if budget_usd > 0 else 0, 1)

                results_today = max(leads_today + wa_today, 1)
                cpr_today = round(spend_today / results_today, 2) if spend_today > 0 else 0

                results_life = max(leads_life + wa_life, 1)
                cpr_life = round(spend_life / results_life, 2) if spend_life > 0 and (leads_life + wa_life) > 0 else 0

                # Adset details including their own today metrics
                adset_list = []
                for a in adsets:
                    a_ins = (a.get("insights", {}).get("data") or [{}])[0]
                    a_actions = a_ins.get("actions") or []
                    a_wa, a_leads = _extract_results(a_actions, WA_TYPES, LEAD_TYPES)
                    tgt = a.get("targeting") or {}
                    geo = tgt.get("geo_locations", {})
                    adset_list.append({
                        "id":               a["id"],
                        "name":             a.get("name", ""),
                        "daily_budget_usd": int(a.get("daily_budget", 0)) / 100,
                        "status":           a.get("effective_status", a.get("status", "")),
                        "optimization_goal":a.get("optimization_goal", ""),
                        "countries":        geo.get("countries", []),
                        "age_min":          tgt.get("age_min"),
                        "age_max":          tgt.get("age_max"),
                        "today": {
                            "spend_usd":   float(a_ins.get("spend", 0)),
                            "impressions": int(a_ins.get("impressions", 0)),
                            "clicks":      int(a_ins.get("clicks", 0)),
                            "ctr":         float(a_ins.get("ctr", 0)),
                            "frequency":   float(a_ins.get("frequency", 0)),
                            "wa_convos":   a_wa,
                            "leads":       a_leads,
                        },
                    })

                result.append({
                    "account":    label,
                    "account_id": raw_id,
                    "id":         c["id"],
                    "name":       c.get("name", ""),
                    "objective":  c.get("objective", ""),
                    "status":     c.get("effective_status", c.get("status", "")),
                    "adsets":     adset_list,
                    "today": {
                        "spend_usd":   spend_today,
                        "impressions": int(ins_today.get("impressions", 0)),
                        "reach":       int(ins_today.get("reach", 0)),
                        "clicks":      int(ins_today.get("clicks", 0)),
                        "ctr":         float(ins_today.get("ctr", 0)),
                        "cpc":         float(ins_today.get("cpc", 0)),
                        "cpm":         float(ins_today.get("cpm", 0)),
                        "frequency":   float(ins_today.get("frequency", 0)),
                        "wa_convos":   wa_today,
                        "leads":       leads_today,
                        "cpr":         cpr_today,
                    },
                    "lifetime": {
                        "spend_usd":   spend_life,
                        "impressions": int(ins_life.get("impressions", 0)),
                        "reach":       int(ins_life.get("reach", 0)),
                        "clicks":      int(ins_life.get("clicks", 0)),
                        "ctr":         float(ins_life.get("ctr", 0)),
                        "cpc":         float(ins_life.get("cpc", 0)),
                        "frequency":   float(ins_life.get("frequency", 0)),
                        "wa_convos":   wa_life,
                        "leads":       leads_life,
                        "cpr":         cpr_life,
                    },
                    "budget_usd": budget_usd,
                    "pct_spent":  pct_spent,
                })
        except Exception as e:
            result.append({"account": label, "error": str(e)})

    return jsonify(result)


@app.route("/api/campaigns/<campaign_id>/pause", methods=["POST"])
def campaign_pause(campaign_id):
    import ads_writer
    try:
        r = ads_writer.update_status(campaign_id, "PAUSED")
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/campaigns/<campaign_id>/resume", methods=["POST"])
def campaign_resume(campaign_id):
    import ads_writer
    try:
        r = ads_writer.update_status(campaign_id, "ACTIVE")
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/adsets/<adset_id>/budget", methods=["POST"])
def adset_budget(adset_id):
    import ads_writer
    data = request.json or {}
    daily = data.get("daily_budget_usd")
    if not daily:
        return jsonify({"ok": False, "error": "daily_budget_usd required"}), 400
    try:
        r = ads_writer.update_budget(adset_id, daily_budget_usd=float(daily))
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Instagram Page Audit ──────────────────────────────────────────────────────
import ig_audit as _iga

_iga.init_db()


@app.route("/api/ig-audit/fetch", methods=["POST"])
def ig_audit_fetch():
    """Fetch a batch of 100 IG feed posts by offset and store locally."""
    body = request.json if request.is_json else {}
    offset = int(body.get("offset", 0))
    count_only = bool(body.get("count_only", False))
    try:
        all_posts = _iga.fetch_all_posts()
        total = len(all_posts)
        if count_only:
            return jsonify({"ok": True, "total_posts": total, "fetched": 0})
        batch = all_posts[offset: offset + 100]
        stored = _iga.store_posts(batch, batch_offset=offset)
        return jsonify({
            "ok": True,
            "fetched": stored,
            "offset": offset,
            "total_posts": total,
            "has_more": offset + 100 < total,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ig-audit/stats")
def ig_audit_stats():
    """Fast DB-only stats — which batches are loaded, total stored."""
    return jsonify(_iga.get_audit_stats())


@app.route("/api/ig-audit/analyze", methods=["POST"])
def ig_audit_analyze():
    """Run AI analysis on all stored unanalyzed posts."""
    try:
        with _iga._conn() as db:
            unanalyzed = [
                dict(r) for r in db.execute(
                    "SELECT * FROM ig_posts WHERE ai_verdict IS NULL ORDER BY timestamp ASC"
                ).fetchall()
            ]
        if not unanalyzed:
            return jsonify({"ok": True, "analyzed": 0, "message": "All posts already analyzed"})
        verdicts = _iga.analyze_posts_batch(unanalyzed)
        _iga.save_verdicts(verdicts)
        counts = {}
        for v in verdicts:
            k = v.get("verdict", "KEEP")
            counts[k] = counts.get(k, 0) + 1
        return jsonify({"ok": True, "analyzed": len(verdicts), "verdicts": counts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ig-audit/posts")
def ig_audit_posts():
    verdict      = request.args.get("verdict")
    all_         = request.args.get("all") == "1"
    batch_offset = request.args.get("batch_offset")
    if all_:
        posts = _iga.get_audit_posts(verdict_filter=verdict, limit=10000)
    elif batch_offset is not None:
        posts = _iga.get_audit_posts(verdict_filter=verdict, batch_offset=int(batch_offset))
    else:
        posts = _iga.get_audit_posts(verdict_filter=verdict, limit=200)
    return jsonify(posts)


@app.route("/api/ig-audit/delete/<ig_post_id>", methods=["POST"])
def ig_audit_delete(ig_post_id):
    result = _iga.delete_ig_post(ig_post_id)
    return jsonify(result)


@app.route("/api/ig-audit/dismiss/<ig_post_id>", methods=["POST"])
def ig_audit_dismiss(ig_post_id):
    _iga.dismiss_post(ig_post_id)
    return jsonify({"ok": True})


@app.route("/api/ig-audit/edit/<ig_post_id>", methods=["POST"])
def ig_audit_edit(ig_post_id):
    caption = request.json.get("caption", "")
    if not caption:
        return jsonify({"ok": False, "error": "caption required"}), 400
    result = _iga.update_ig_caption(ig_post_id, caption)
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/ig-audit/keep/<ig_post_id>", methods=["POST"])
def ig_audit_keep(ig_post_id):
    _iga.mark_kept(ig_post_id)
    return jsonify({"ok": True})


@app.route("/api/ig-audit/reset-errors", methods=["POST"])
def ig_audit_reset_errors():
    """Reset posts that failed analysis so they can be re-analyzed."""
    with _iga._conn() as db:
        result = db.execute(
            "UPDATE ig_posts SET ai_verdict=NULL, ai_reason=NULL, ai_new_caption=NULL, audited_at=NULL "
            "WHERE ai_verdict='ERROR' OR ai_reason LIKE 'Analysis error%' OR ai_reason LIKE 'Parse error%' OR ai_reason LIKE 'Worker error%' OR ai_reason LIKE 'No response%'"
        )
        reset_count = result.rowcount
        db.commit()
    return jsonify({"ok": True, "reset": reset_count})


@app.route("/api/ig-audit/clear-all", methods=["POST"])
def ig_audit_clear_all():
    """Wipe all stored posts so batches can be re-fetched cleanly."""
    with _iga._conn() as db:
        db.execute("DELETE FROM ig_posts")
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/ig-audit/download-images", methods=["POST"])
def ig_audit_download_images():
    """
    Re-download images for posts that don't have a local cache yet.
    Call this for posts fetched before the local-cache feature was added.
    """
    with _iga._conn() as db:
        rows = db.execute(
            "SELECT ig_post_id, media_url, thumbnail_url FROM ig_posts "
            "WHERE (local_image_path IS NULL OR local_image_path='') "
            "AND (media_url!='' OR thumbnail_url!='')"
        ).fetchall()

    posts = [{"id": r[0], "ig_post_id": r[0],
              "media_url": r[1], "thumbnail_url": r[2]} for r in rows]
    if not posts:
        return jsonify({"ok": True, "downloaded": 0, "message": "All images already cached"})

    paths = _iga._download_images_parallel(posts)

    # Save paths back to DB
    with _iga._conn() as db:
        for pid, path in paths.items():
            db.execute(
                "UPDATE ig_posts SET local_image_path=? WHERE ig_post_id=?",
                (path, pid)
            )
        db.commit()

    return jsonify({"ok": True, "downloaded": len(paths), "total_missing": len(posts)})


@app.route("/api/ig-audit/top-posts")
def ig_audit_top_posts():
    limit = int(request.args.get("limit", 12))
    posts = _iga.get_top_posts(limit=limit)
    return jsonify({"posts": posts})


@app.route("/api/ig-audit/content-prompts", methods=["POST"])
def ig_audit_content_prompts():
    top_posts = _iga.get_top_posts(limit=8)
    if not top_posts:
        return jsonify({"error": "No posts in DB — fetch at least one batch first"}), 400
    try:
        prompts = _iga.generate_content_prompts(top_posts)
        return jsonify({"prompts": prompts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Platform Intelligence ─────────────────────────────────────────────────────

@app.route("/api/platforms/research", methods=["POST"])
def platforms_research():
    """
    Fetch public profile data for a platform + generate AI content strategy.
    Body: { "platform": "pinterest" | "linkedin" }
    """
    from platform_brain import (
        fetch_pinterest_profile, fetch_linkedin_company, generate_platform_strategy
    )
    import re as _re

    platform = (request.json or {}).get("platform", "").lower()
    if platform not in ("pinterest", "linkedin"):
        return jsonify({"error": "platform must be 'pinterest' or 'linkedin'"}), 400

    # Build IG performance context from cache
    cache = load_cache()
    ig_ctx_lines = []
    ads = cache.get("ads", {})
    if ads.get("accounts"):
        for acct in ads["accounts"]:
            if acct.get("error"):
                continue
            for ad in acct.get("ads", [])[:4]:
                ins = (ad.get("insights", {}).get("data") or [{}])[0]
                cr  = ad.get("creative", {}) or {}
                spec = cr.get("object_story_spec", {}) or {}
                ld   = spec.get("link_data") or spec.get("video_data") or {}
                body = (cr.get("body") or ld.get("message") or "")[:100]
                ig_ctx_lines.append(
                    f"  [{(ad.get('campaign') or {}).get('objective', '')}] "
                    f"CTR:{float(ins.get('ctr',0)):.1f}% Spend:${float(ins.get('spend',0)):.0f} "
                    f"Copy snippet: {body[:80]}"
                )

    # Also include top IG posts if available
    try:
        top_ig = _iga.get_top_posts(limit=5)
        for p in top_ig:
            cap = (p.get("caption") or "")[:80].replace("\n", " ")
            ig_ctx_lines.append(
                f"  IG top post — ❤️{p.get('like_count',0)} 💬{p.get('comments_count',0)} | {cap}"
            )
    except Exception:
        pass

    ig_context = "\n".join(ig_ctx_lines)

    # Fetch profile data
    if platform == "pinterest":
        profile = fetch_pinterest_profile("khalesae")
    else:
        profile = fetch_linkedin_company("khales-ae")

    # Generate strategy
    try:
        strategy = generate_platform_strategy(platform, profile, ig_context)
    except Exception as e:
        strategy = {"error": str(e)}

    return jsonify({"platform": platform, "profile": profile, "strategy": strategy})


# ── Serve cached IG images ───────────────────────────────────────────────────

@app.route("/ig-image/<ig_post_id>")
def serve_ig_image(ig_post_id):
    """Serve a locally cached Instagram post image by post ID."""
    from flask import send_file, abort
    with _iga._conn() as db:
        row = db.execute(
            "SELECT local_image_path FROM ig_posts WHERE ig_post_id=?", (ig_post_id,)
        ).fetchone()
    if not row or not row[0]:
        abort(404)
    path = Path(row[0])
    if not path.exists():
        abort(404)
    return send_file(str(path))


# ── Briefing endpoint ────────────────────────────────────────────────────────

@app.route("/api/briefing")
def briefing():
    from briefing import get_briefing
    return jsonify(get_briefing())


# ── Central Chat Controller ───────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat_controller():
    """
    Central chat endpoint — routes natural language commands to all agents.
    Body: { "message": str, "history": [...], "image_base64"?: str, "image_media_type"?: str }
    """
    import uuid as _uuid
    body = request.json or {}
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    image_base64 = body.get("image_base64") or None
    image_media_type = body.get("image_media_type") or "image/jpeg"

    if not message and not image_base64:
        return jsonify({"error": "Empty message"}), 400

    # Save uploaded image to disk so tools can reference it
    image_path = None
    if image_base64:
        try:
            import base64 as _b64
            from pathlib import Path as _P
            uploads = _P("uploads")
            uploads.mkdir(exist_ok=True)
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(image_media_type, ".jpg")
            image_path = str(uploads / f"{_uuid.uuid4().hex}{ext}")
            with open(image_path, "wb") as fh:
                fh.write(_b64.b64decode(image_base64))
        except Exception:
            image_path = None

    try:
        from chat_brain import process_message
        result = process_message(
            message, history,
            image_base64=image_base64,
            image_media_type=image_media_type,
            image_path=image_path,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "content": f"Error: {e}", "data": None, "data_type": None}), 500


@app.route("/api/seo-chat", methods=["POST"])
def seo_chat():
    body = request.json or {}
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not message:
        return jsonify({"error": "Empty message"}), 400
    try:
        from seo_brain import process_seo_message
        result = process_seo_message(message, history)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "content": f"Error: {e}", "data": None}), 500


if __name__ == "__main__":
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.auto_reload = True
    app.run(debug=False, port=5000, use_reloader=False, threaded=True)