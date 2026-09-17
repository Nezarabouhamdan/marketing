"""Researcher Agent — unified Instagram + Facebook + Competitors + Monitor intelligence."""
import json
import re
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic

claude = Anthropic()
CACHE_FILE = Path("daily_cache.json")
RESEARCHER_FILE = Path("latest_researcher.json")


def build_unified_brief(cache: dict) -> str:
    parts = []

    # Instagram
    report = cache.get("report", {})
    if report:
        acc = report.get("account", {})
        ins = report.get("insights", {})
        posts = report.get("posts", [])[:5]
        post_lines = "\n".join(
            f"  [{p.get('media_type')}] {(p.get('timestamp') or '')[:10]} "
            f"→ ❤️ {p.get('like_count', 0)} 💬 {p.get('comments_count', 0)} "
            f"| {(p.get('caption') or '')[:100]}"
            for p in posts
        )
        parts.append(
            f"━━━ INSTAGRAM (@{acc.get('username')}) ━━━\n"
            f"Followers: {acc.get('followers_count', 0):,} | Posts: {acc.get('media_count', 0):,}\n"
            f"Last 7d: Reach {ins.get('reach', 0):,} | Profile views {ins.get('profile_views', 0):,} "
            f"| Engaged accounts {ins.get('accounts_engaged', 0):,} | New followers +{ins.get('new_followers', 0)}\n\n"
            f"Recent posts:\n{post_lines}"
        )

    # Facebook
    fb = cache.get("facebook", {})
    if fb:
        info = fb.get("info", {})
        fbi = fb.get("insights", {})
        fb_posts = fb.get("posts", [])[:3]
        fb_lines = "\n".join(
            f"  {(p.get('created_time') or '')[:10]} ❤️{p.get('reaction_count', 0)} "
            f"💬{p.get('comment_count', 0)} 🔁{p.get('share_count', 0)} "
            f"— {(p.get('message') or '')[:80]}"
            for p in fb_posts
        )
        parts.append(
            f"━━━ FACEBOOK PAGE ━━━\n"
            f"{info.get('name')} — {info.get('fan_count', 0):,} fans\n"
            f"Last 7d: Impressions {fbi.get('page_impressions', 0):,} | "
            f"Engagements {fbi.get('page_post_engagements', 0):,} | "
            f"New fans +{fbi.get('page_fan_adds', 0)}\n"
            f"Recent posts:\n{fb_lines}"
        )

    # Competitors
    comp = cache.get("competitors", {})
    if comp and comp.get("competitors"):
        valid = [c for c in comp["competitors"] if "error" not in c]
        if valid:
            comp_lines = []
            for c in valid:
                tp = c.get("top_post") or {}
                comp_lines.append(
                    f"  @{c.get('username')}: {c.get('followers_count', 0):,} followers | "
                    f"{c.get('posts_per_week', '?')} posts/wk | ER {c.get('engagement_rate', 0)}% | "
                    f"top post ❤️{tp.get('likes', 0)}"
                )
            parts.append("━━━ COMPETITORS ━━━\n" + "\n".join(comp_lines))

    # Monitor (hashtags + mentions)
    mon = cache.get("monitor", {})
    if mon:
        tag_lines = []
        for tag in (mon.get("hashtags") or [])[:5]:
            if tag.get("error"):
                continue
            top = (tag.get("posts") or [])[:2]
            tag_lines.append(f"  #{tag['hashtag']}:")
            for p in top:
                tag_lines.append(f"    ❤️ {p.get('like_count', 0)} | {(p.get('caption') or '')[:80]}")
        if tag_lines:
            parts.append("━━━ HASHTAG TRENDS ━━━\n" + "\n".join(tag_lines))

        mentions = (mon.get("mentions") or [])[:5]
        if mentions:
            m_lines = [
                f"  @{m.get('username')} ({(m.get('timestamp') or '')[:10]}): {(m.get('caption') or '')[:80]}"
                for m in mentions
            ]
            parts.append("━━━ MENTIONS OF KHALES ━━━\n" + "\n".join(m_lines))

    return "\n\n".join(parts) or "(no data available — run scheduler to populate cache)"


_SYSTEM = """You are the Lead Researcher for Khales Group, a UAE luxury engineering & construction firm targeting HNW investors.

You receive raw data from 4 sources: Instagram, Facebook, Competitors, Hashtag/Mention monitoring. Your job: synthesize into ONE unified executive brief.

Output clean HTML using only <h3>, <p>, <ul>, <li>, <strong>.

Structure:
<h3>📌 Headline</h3>
<p>One-sentence summary of where Khales stands this week across all channels.</p>

<h3>🟢 What's Working</h3>
<ul><li>Cross-channel wins (e.g., "Instagram reach up + competitor X has same content style winning")</li></ul>

<h3>🔴 What's Concerning</h3>
<ul><li>Cross-channel concerns</li></ul>

<h3>💡 Cross-Channel Insights</h3>
<ul><li>Insights you can ONLY see by looking at all 4 sources together (e.g., "competitors using #X are getting 3x our reach — we don't use that tag")</li></ul>

<h3>🎯 This Week's Focus</h3>
<p>One paragraph: where to spend energy this week based on ALL signals.</p>

Be specific, reference actual data points. Under 350 words. No fluff."""


def generate_unified_summary(brief: str) -> str:
    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": brief}],
    )
    return response.content[0].text


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()


def run_researcher(cache: dict = None) -> dict:
    """Generate unified research output. Saves to latest_researcher.json. Returns result dict."""
    if cache is None:
        if not CACHE_FILE.exists():
            return {"error": "No cache yet. Run 'python scheduler.py' to populate."}
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    brief = build_unified_brief(cache)
    unified_summary = generate_unified_summary(brief)

    # Per-section analyses — reuse cached ones if present, don't regenerate
    sections = {
        "instagram_analysis": (cache.get("report") or {}).get("analysis", ""),
        "competitors_analysis": (cache.get("competitors") or {}).get("analysis", ""),
        "monitor_analysis": (cache.get("monitor") or {}).get("analysis", ""),
    }

    result = {
        "generated_at": datetime.now().isoformat(),
        "unified_summary_html": unified_summary,
        "sections": sections,
        "raw_data": {
            "instagram": cache.get("report", {}),
            "facebook": cache.get("facebook", {}),
            "competitors": cache.get("competitors", {}),
            "monitor": cache.get("monitor", {}),
        },
    }

    with open(RESEARCHER_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)

    return result


if __name__ == "__main__":
    print("Generating unified researcher report...")
    result = run_researcher()
    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"Saved to {RESEARCHER_FILE}")
        preview = _strip_html(result["unified_summary_html"])[:400]
        print(preview)
