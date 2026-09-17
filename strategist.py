"""
The Strategist — synthesizes all data sources into a unified marketing plan.
Reads the daily cache (Researcher + Analyst output) and produces a weekly strategy.
Regenerates once per week (Monday) only.
"""
import json
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

claude = Anthropic()
CACHE_FILE = Path("daily_cache.json")
STRATEGY_FILE = Path("latest_strategy.json")


def strategy_is_current() -> bool:
    """Return True if a strategy already exists for the current ISO week."""
    if not STRATEGY_FILE.exists():
        return False
    try:
        data = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(data.get("generated_at", ""))
        now = datetime.now()
        return (generated.year == now.year and
                generated.isocalendar()[1] == now.isocalendar()[1])
    except Exception:
        return False


def build_intelligence_brief(cache):
    """Convert the cache into a clean intelligence brief for Claude."""

    sections = []

    # 1. Our Instagram performance
    report = cache.get("report", {})
    if report:
        acc = report.get("account", {})
        ins = report.get("insights", {})
        posts = report.get("posts", [])[:5]
        sections.append(f"""
═══ KHALES INSTAGRAM PERFORMANCE (last 7 days) ═══
@{acc.get('username')} — {acc.get('followers_count', 0):,} followers
Reach: {ins.get('reach', 0):,} | Profile views: {ins.get('profile_views', 0):,}
Engaged accounts: {ins.get('accounts_engaged', 0):,} | New followers: {ins.get('new_followers', 0):,}

Recent posts:
{chr(10).join(f"  - [{p.get('media_type')}] {p.get('like_count', 0)} likes, {p.get('comments_count', 0)} comments — {(p.get('caption') or '')[:120]}" for p in posts)}
""")

    # 2. Facebook
    fb = cache.get("facebook", {})
    if fb:
        info = fb.get("info", {})
        fbi = fb.get("insights", {})
        sections.append(f"""
═══ KHALES FACEBOOK PAGE ═══
{info.get('name')} — {info.get('fan_count', 0):,} fans
Last 7d: Impressions {fbi.get('page_impressions', 0):,} | Engagements {fbi.get('page_post_engagements', 0):,} | New fans +{fbi.get('page_fan_adds', 0)}
""")

    # 3. Top ads
    ads = cache.get("ads", {})
    if ads and ads.get("accounts"):
        ad_lines = []
        for acct in ads["accounts"]:
            if acct.get("error"):
                continue
            for ad in acct.get("ads", [])[:3]:
                aci = (ad.get("insights", {}).get("data") or [{}])[0]
                cr = ad.get("creative", {})
                spec = cr.get("object_story_spec", {}) or {}
                link_data = spec.get("link_data") or spec.get("video_data") or {}
                body = (cr.get("body") or link_data.get("message") or "")[:200]
                tgt = (ad.get("adset") or {}).get("targeting", {}) or {}
                geo = tgt.get("geo_locations", {}) or {}
                ad_lines.append(
                    f"  - [{(ad.get('campaign') or {}).get('objective')}] {ad.get('name', '')[:60]}\n"
                    f"    Spend: ${float(aci.get('spend', 0)):.0f} | CTR: {float(aci.get('ctr', 0)):.2f}% | CPC: ${float(aci.get('cpc', 0)):.2f}\n"
                    f"    Targeting: {geo.get('countries', [])} cities={[c.get('name') for c in geo.get('cities', [])][:3]} age={tgt.get('age_min', '?')}-{tgt.get('age_max', '?')}\n"
                    f"    Creative: \"{body}\""
                )
        if ad_lines:
            sections.append(f"""
═══ TOP-PERFORMING ADS (across both ad accounts) ═══
{chr(10).join(ad_lines)}
""")

    # 4. Competitors
    comp = cache.get("competitors", {})
    if comp and comp.get("competitors"):
        comp_lines = []
        for c in comp["competitors"]:
            if c.get("error"):
                continue
            tp = c.get("top_post") or {}
            comp_lines.append(
                f"  - @{c.get('username')}: {c.get('followers_count', 0):,} followers, "
                f"{c.get('posts_per_week', '?')} posts/wk, {c.get('engagement_rate', 0)}% ER"
                f" | Top post: ❤️{tp.get('likes', 0)} \"{(tp.get('caption') or '')[:100]}\""
            )
        if comp_lines:
            sections.append(f"""
═══ COMPETITORS ═══
{chr(10).join(comp_lines)}
""")

    # 5. Hashtag landscape + mentions
    mon = cache.get("monitor", {})
    if mon:
        tag_lines = []
        for tag in mon.get("hashtags", [])[:5]:
            if tag.get("error"):
                continue
            top = (tag.get("posts") or [])[:3]
            tag_lines.append(f"  #{tag['hashtag']}:")
            for p in top:
                tag_lines.append(
                    f"    ❤️{p.get('like_count', 0)} 💬{p.get('comments_count', 0)} — {(p.get('caption') or '')[:100]}"
                )

        mentions = mon.get("mentions", [])[:5]
        m_lines = [f"  - @{m.get('username')}: {(m.get('caption') or '')[:100]}" for m in mentions]

        sections.append(f"""
═══ HASHTAG LANDSCAPE (top posts in our niche) ═══
{chr(10).join(tag_lines)}

═══ RECENT MENTIONS OF KHALES ═══
{chr(10).join(m_lines) if m_lines else "  (none recent)"}
""")

    # 6. What was actually posted in the last 2 weeks (prevents repeating topics)
    try:
        import sqlite3
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        db = sqlite3.connect("scheduled_posts.db")
        db.row_factory = sqlite3.Row
        recent = db.execute(
            "SELECT format, tier, caption, scheduled_for, status FROM scheduled_posts "
            "WHERE DATE(scheduled_for) >= ? AND status NOT IN ('CANCELLED','FAILED') "
            "ORDER BY scheduled_for DESC",
            (cutoff,)
        ).fetchall()
        db.close()
        if recent:
            post_lines = [
                f"  [{r['status']}] {r['scheduled_for'][:10]} {r['format']} ({r['tier']}) — "
                f"\"{(r['caption'] or '')[:120].replace(chr(10), ' ')}\""
                for r in recent
            ]
            sections.append(f"""
═══ WHAT WAS ACTUALLY POSTED (last 14 days) — DO NOT REPEAT THESE TOPICS ═══
{chr(10).join(post_lines)}
""")
    except Exception:
        pass

    # 7. Last week's strategy (so we build forward, not repeat)
    try:
        if STRATEGY_FILE.exists():
            prev = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
            prev_date = prev.get("generated_at", "")[:10]
            prev_html = prev.get("strategy_html", "")
            import re
            prev_text = re.sub(r"<[^>]+>", " ", prev_html).strip()[:1500]
            sections.append(f"""
═══ LAST WEEK'S STRATEGY (generated {prev_date}) — BUILD FORWARD FROM THIS ═══
{prev_text}
""")
    except Exception:
        pass

    return "\n".join(sections)


def generate_strategy(cache=None):
    """Run the strategist on the latest cache. Returns structured plan."""
    if cache is None:
        if not CACHE_FILE.exists():
            return {"error": "No cache yet. Run scheduler.py first."}
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    brief = build_intelligence_brief(cache)

    system = """You are the Marketing Strategist for Khales, a UAE luxury engineering & construction firm targeting HNW investors.

You receive intelligence briefs from the Researcher and Analyst agents. Your job: synthesize across all sources and produce a CONCRETE 7-day marketing plan that the Marketing team can execute.

CONTEXT — KHALES BUSINESS:
- Target audience: HNW investors in UAE (Dubai, Abu Dhabi, Sharjah, Fujairah)
- Service: luxury villa design, interior design, construction project management
- Two tiers: Khales Signature (premium) and Khales Elite (top, UHNW)
- Engagement rates in this niche are LOW (0.1-1%) — that's normal. Don't chase likes. Chase saves, DMs, qualified leads.

OUTPUT FORMAT — clean HTML using only: <h2>, <h3>, <p>, <ul>, <li>, <strong>, <table>, <tr>, <th>, <td>.

Structure your strategy like this:

<h2>📌 Strategic Direction (this week)</h2>
<p>2-3 sentences on the overall theme/positioning to push this week, based on what's working and what competitors are doing.</p>

<h2>📅 Posting Schedule (next 7 days)</h2>
<table>
<tr><th>Day</th><th>Format</th><th>Topic / Angle</th><th>Tier</th><th>Caption Direction</th><th>Hashtags</th></tr>
<tr><td>Mon</td><td>Carousel</td><td>...</td><td>Signature</td><td>...</td><td>#...</td></tr>
... (5-7 posts total)
</table>

<h2>💰 Ad Recommendations</h2>
<h3>Continue / Scale</h3>
<ul><li>Ad name + why + budget recommendation</li></ul>
<h3>Pause / Kill</h3>
<ul><li>Ad name + why</li></ul>
<h3>New Campaign to Launch</h3>
<p>Objective, audience, creative direction, hashtags, suggested daily budget.</p>

<h2>🎨 Creative Direction</h2>
<ul><li>Specific visual / messaging directions based on what's winning across our top posts AND competitors' top posts</li></ul>

<h2>🤝 Engagement Actions</h2>
<ul><li>Specific accounts to engage with (mentions, hashtag-using accounts) and how</li></ul>

<h2>⚠️ What to Avoid</h2>
<ul><li>Patterns the data shows are NOT working</li></ul>

<h2>📊 Success Metrics for This Week</h2>
<ul><li>Specific, measurable KPI targets to hit</li></ul>

RULES:
- Be SPECIFIC. Reference actual ad names, competitor handles, post types.
- Be ACTIONABLE. "Post a carousel about X on Tuesday at 6pm" not "post more carousels".
- Be HONEST. If data is too thin to recommend something, say so.
- If a competitor's specific post performed well, suggest a Khales angle on the same theme.
- Total length: under 800 words."""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": brief}],
    )

    strategy_html = response.content[0].text

    result = {
        "generated_at": datetime.now().isoformat(),
        "strategy_html": strategy_html,
        "intelligence_brief": brief,
    }

    with open(STRATEGY_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)

    return result


if __name__ == "__main__":
    print("🎯 Generating marketing strategy...")
    result = generate_strategy()
    if "error" in result:
        print(f"❌ {result['error']}")
    else:
        print(f"✅ Strategy generated. Saved to {STRATEGY_FILE}")
        print("\n" + "=" * 60)
        print(result["strategy_html"][:1000] + "...")
