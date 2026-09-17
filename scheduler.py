"""Daily auto-refresh of all dashboard data."""
import os
import json
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler

# Cache file — stores latest results so dashboard loads instantly
CACHE_FILE = Path("daily_cache.json")
LOG_FILE = Path("scheduler.log")


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save_cache(data):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)


def load_cache():
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def daily_refresh():
    """Run all 5 reports and cache the results."""
    log("🔄 Starting daily refresh...")

    # Import here to avoid circular imports
    from app import (
        fetch_account, fetch_posts, fetch_insights,
        get_claude_analysis, fetch_top_ads, AD_ACCOUNTS,
        analyze_ads_with_claude, fetch_page_info, fetch_page_posts,
        fetch_page_insights, fetch_competitors,
        analyze_competitors_with_claude, fetch_all_hashtags,
        fetch_mentions, analyze_monitoring_with_claude, claude
    )
    from token_manager import assert_token_healthy

    cache = {"refreshed_at": datetime.now().isoformat(), "errors": []}

    try:
        assert_token_healthy()
    except Exception as e:
        log(f"❌ Token unhealthy: {e}")
        cache["errors"].append(f"token: {e}")
        save_cache(cache)
        return

    # 1. Instagram report
    try:
        log("  📈 Instagram report...")
        account = fetch_account()
        posts = fetch_posts(limit=10)
        insights = fetch_insights(days=7)
        analysis = get_claude_analysis(account, posts, insights, 7)
        cache["report"] = {
            "account": account, "posts": posts, "insights": insights,
            "analysis": analysis, "days": 7,
        }
        log("  ✅ Instagram done")
    except Exception as e:
        log(f"  ❌ Instagram failed: {e}")
        cache["errors"].append(f"instagram: {e}")

    # 2. Ads
    try:
        log("  💰 Ads...")
        ads_results = []
        for label, acct_id in AD_ACCOUNTS:
            if not acct_id:
                continue
            top = fetch_top_ads(acct_id, limit=3)
            if isinstance(top, dict) and "error" in top:
                ads_results.append({"account": label, "error": str(top["error"])})
            else:
                ads_results.append({"account": label, "account_id": acct_id, "ads": top})
        ads_analysis = analyze_ads_with_claude(ads_results)
        cache["ads"] = {"accounts": ads_results, "analysis": ads_analysis}
        log("  ✅ Ads done")
    except Exception as e:
        log(f"  ❌ Ads failed: {e}")
        cache["errors"].append(f"ads: {e}")

    # 3. Facebook
    try:
        log("  📘 Facebook...")
        fb_info = fetch_page_info()
        fb_posts = fetch_page_posts(limit=10)
        fb_insights = fetch_page_insights(days=7)
        # Reuse the FB analysis logic from app.py route
        from app import facebook_report  # not ideal but works
        cache["facebook"] = {
            "info": fb_info, "posts": fb_posts, "insights": fb_insights,
            "days": 7,
        }
        log("  ✅ Facebook done (basic — analysis runs on view)")
    except Exception as e:
        log(f"  ❌ Facebook failed: {e}")
        cache["errors"].append(f"facebook: {e}")

    # 4. Competitors
    try:
        log("  🎯 Competitors...")
        our_account = cache.get("report", {}).get("account") or fetch_account()
        competitors = fetch_competitors()
        comp_analysis = analyze_competitors_with_claude(competitors, our_account)
        cache["competitors"] = {
            "us": our_account, "competitors": competitors, "analysis": comp_analysis,
        }
        log("  ✅ Competitors done")
    except Exception as e:
        log(f"  ❌ Competitors failed: {e}")
        cache["errors"].append(f"competitors: {e}")

    # 5. Monitor
    try:
        log("  🏷️ Monitor...")
        hashtags = fetch_all_hashtags(kind="top", limit=10)
        mentions = fetch_mentions(limit=20)
        mon_analysis = analyze_monitoring_with_claude(hashtags, mentions)
        cache["monitor"] = {
            "hashtags": hashtags,
            "mentions": mentions.get("mentions", []),
            "analysis": mon_analysis,
        }
        log("  ✅ Monitor done")
    except Exception as e:
        log(f"  ❌ Monitor failed: {e}")
        cache["errors"].append(f"monitor: {e}")

    # 6. Researcher — unified cross-channel intelligence brief
    try:
        log("  Researcher synthesizing...")
        from researcher import run_researcher
        researcher_result = run_researcher(cache=cache)
        if "error" not in researcher_result:
            cache["researcher"] = researcher_result
            log("  Researcher done")
        else:
            log(f"  Researcher: {researcher_result['error']}")
    except Exception as e:
        log(f"  Researcher failed: {e}")
        cache["errors"].append(f"researcher: {e}")

    # 7. Strategist — weekly only (skips if already generated this ISO week)
    try:
        from strategist import generate_strategy, strategy_is_current
        if strategy_is_current():
            log("  ⏭️  Strategy already current for this week — skipping")
        else:
            log("  🎯 Strategist generating new weekly plan...")
            strategy_result = generate_strategy(cache=cache)
            if "error" not in strategy_result:
                cache["strategy"] = strategy_result
                log("  ✅ Strategy done")
            else:
                log(f"  ⚠️ Strategy: {strategy_result['error']}")
    except Exception as e:
        log(f"  ❌ Strategist failed: {e}")
        cache["errors"].append(f"strategist: {e}")

    save_cache(cache)
    log(f"🎉 Daily refresh complete. Errors: {len(cache['errors'])}")


def publish_due_posts():
    """Check for scheduled posts whose time has arrived and publish them."""
    from posts_db import get_due_posts
    from publisher import publish_post

    due = get_due_posts()
    for post in due:
        try:
            log(f"  [publish] Post #{post['id']} due — publishing...")
            result = publish_post(post["id"])
            if result.get("published"):
                log(f"  [publish] Post #{post['id']} OK: {result.get('permalink', '')}")
            elif result.get("skipped"):
                log(f"  [publish] Post #{post['id']} skipped (status={result.get('status')})")
            else:
                log(f"  [publish] Post #{post['id']} FAILED: {result.get('error', '')}")
        except Exception as e:
            log(f"  [publish] Post #{post['id']} exception: {e}")


def run_ad_proposer():
    """Generate daily ad proposals at 8:15 AM (after daily_refresh completes)."""
    log("  [ad_proposer] Generating proposals...")
    try:
        from ads_proposer import generate_daily_proposals
        ids = generate_daily_proposals()
        log(f"  [ad_proposer] Done — {len(ids)} proposals created: {ids}")
    except Exception as e:
        log(f"  [ad_proposer] Failed: {e}")


def run_campaign_brain():
    """Sync campaign history and generate AI learnings for finished campaigns."""
    log("  [campaign_brain] Syncing campaigns...")
    try:
        from campaign_brain import sync_campaigns, analyze_and_learn
        from ads_analysis import AD_ACCOUNTS
        synced = sync_campaigns(AD_ACCOUNTS)
        analyzed = analyze_and_learn(limit=10)
        log(f"  [campaign_brain] Done — {synced} synced, {analyzed} analyzed")
    except Exception as e:
        log(f"  [campaign_brain] Failed: {e}")


def start_scheduler():
    """Start background scheduler. Runs daily at 8 AM + publish check every minute."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(daily_refresh, "cron", hour=8, minute=0, id="daily_refresh")
    scheduler.add_job(run_ad_proposer, "cron", hour=8, minute=15, id="ad_proposer")
    scheduler.add_job(run_campaign_brain, "cron", hour=8, minute=30, id="campaign_brain")
    scheduler.add_job(publish_due_posts, "interval", minutes=1, id="publish_check")
    scheduler.start()
    log("Scheduler started. daily_refresh=08:00, ad_proposer=08:15, campaign_brain=08:30, publish_check=every 1min")
    return scheduler


if __name__ == "__main__":
    # Run once manually for testing
    print("Running manual refresh...")
    daily_refresh()
    print("\n✅ Done. Check daily_cache.json")
