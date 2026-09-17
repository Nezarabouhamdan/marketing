"""Deep analysis of top-performing ads from each account."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_USER_TOKEN")
AD_ACCOUNTS = [
    ("Account 1", os.getenv("META_AD_ACCOUNT_1")),
    ("Account 2", os.getenv("META_AD_ACCOUNT_2")),
    ("Account 3", os.getenv("META_AD_ACCOUNT_3")),
]


def fetch_top_ads(ad_account_id, limit=3, min_spend=50, min_impressions=1000, min_days_run=3):
    """
    Smarter ranking — paginated to avoid timeouts on large accounts.
    Retries transient errors with exponential backoff.
    """
    import time as _time
    from datetime import datetime

    _FIELDS = (
        "id,name,created_time,campaign{objective},"
        "adset{start_time,end_time,status},"
        "insights.date_preset(maximum){spend,impressions,clicks,ctr,cpc,cpm,actions,"
        "cost_per_action_type,date_start,date_stop}"
    )

    # Paginated light query — 50 ads per page to stay under timeout
    all_ads = []
    url = f"{GRAPH}/{ad_account_id}/ads"
    params = {"fields": _FIELDS, "limit": 50, "access_token": TOKEN}

    page_count = 0
    while url and page_count < 20:
        r = None
        for attempt in range(3):
            try:
                r = requests.get(
                    url,
                    params=params if page_count == 0 else None,
                    timeout=30,
                )
                if r.status_code == 200:
                    break
                _time.sleep(2 ** attempt)
            except requests.exceptions.RequestException:
                _time.sleep(2 ** attempt)

        if r is None or r.status_code != 200:
            if all_ads:
                break  # partial data — score what we have
            return {"error": r.json() if r else {"message": "request failed"}}

        page = r.json()
        all_ads.extend(page.get("data", []))
        url = page.get("paging", {}).get("next")
        params = None  # 'next' URL carries its own params
        page_count += 1

    ads = all_ads

    def days_run(ad):
        """How many days did this ad actually run?"""
        ins = (ad.get("insights", {}).get("data") or [{}])[0]
        start = ins.get("date_start")
        stop = ins.get("date_stop")
        if not start or not stop:
            return 0
        try:
            d1 = datetime.strptime(start, "%Y-%m-%d")
            d2 = datetime.strptime(stop, "%Y-%m-%d")
            return max(1, (d2 - d1).days + 1)
        except Exception:
            return 0

    def score(ad):
        ins = (ad.get("insights", {}).get("data") or [{}])[0]
        spend = float(ins.get("spend", 0))
        impressions = int(ins.get("impressions", 0))
        run_days = days_run(ad)

        if spend < min_spend:
            return -1
        if impressions < min_impressions:
            return -1
        if run_days < min_days_run:
            return -1

        objective = (ad.get("campaign") or {}).get("objective", "")
        ctr = float(ins.get("ctr", 0))
        cpc = float(ins.get("cpc", 0))
        cpm = float(ins.get("cpm", 0))
        actions = ins.get("actions", [])
        cpa_list = ins.get("cost_per_action_type", [])

        def cpa(t):
            for a in cpa_list:
                if a.get("action_type") == t:
                    return float(a.get("value", 0))
            return None

        def count(t):
            for a in actions:
                if a.get("action_type") == t:
                    return float(a.get("value", 0))
            return 0

        if "LEADS" in objective:
            leads = count("lead") + count("onsite_conversion.lead_grouped")
            cpl = cpa("lead") or cpa("onsite_conversion.lead_grouped")
            if leads > 0 and cpl:
                return 10000 / cpl
            return leads * 10

        if "TRAFFIC" in objective or "LINK_CLICKS" in objective:
            return ctr * 10

        if "ENGAGEMENT" in objective:
            eng = count("post_engagement") + count("page_engagement")
            return eng / max(spend, 1)

        if "MESSAGES" in objective:
            msgs = count("onsite_conversion.messaging_conversation_started_7d")
            cpm_msg = cpa("onsite_conversion.messaging_conversation_started_7d")
            if msgs > 0 and cpm_msg:
                return 1000 / cpm_msg
            return msgs

        if "AWARENESS" in objective:
            return 1000 / max(cpm, 0.01)

        return ctr

    scored = [(score(a), days_run(a), a) for a in ads]
    scored = [(s, d, a) for s, d, a in scored if s > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [a for _, _, a in scored[:limit]]

    detailed = []
    for ad in top:
        r2 = requests.get(f"{GRAPH}/{ad['id']}", params={
            "fields": (
                "id,name,status,created_time,effective_status,"
                "adset{name,targeting,daily_budget,lifetime_budget,start_time,end_time,"
                "optimization_goal,billing_event,bid_strategy,bid_amount,destination_type,"
                "promoted_object,attribution_spec,pacing_type,status},"
                "campaign{name,objective,status,daily_budget,lifetime_budget,buying_type,"
                "bid_strategy,start_time,stop_time},"
                "creative{id,name,title,body,image_url,thumbnail_url,video_id,"
                "instagram_permalink_url,object_story_spec,url_tags},"
                "insights.date_preset(maximum){spend,impressions,reach,clicks,ctr,cpc,cpm,"
                "frequency,actions,cost_per_action_type,quality_ranking,"
                "engagement_rate_ranking,conversion_rate_ranking,date_start,date_stop}"
            ),
            "access_token": TOKEN,
        })
        if r2.status_code == 200:
            detailed.append(r2.json())

    return detailed

def fmt_targeting(targeting):
    """Make targeting human-readable."""
    if not targeting:
        return "  (no targeting data)"
    lines = []

    if "geo_locations" in targeting:
        geo = targeting["geo_locations"]
        countries = geo.get("countries", [])
        cities = [c.get("name") for c in geo.get("cities", [])]
        regions = [r.get("name") for r in geo.get("regions", [])]
        if countries: lines.append(f"  📍 Countries: {', '.join(countries)}")
        if cities: lines.append(f"  🏙️  Cities: {', '.join(cities)}")
        if regions: lines.append(f"  🗺️  Regions: {', '.join(regions)}")

    if "age_min" in targeting or "age_max" in targeting:
        lines.append(f"  👤 Age: {targeting.get('age_min', '?')}–{targeting.get('age_max', '?')}")

    genders = targeting.get("genders", [])
    if genders:
        gmap = {1: "Men", 2: "Women"}
        lines.append(f"  ⚧  Gender: {', '.join(gmap.get(g, str(g)) for g in genders)}")

    if "interests" in targeting:
        names = [i.get("name") for i in targeting["interests"]]
        lines.append(f"  💡 Interests: {', '.join(names)}")

    if "behaviors" in targeting:
        names = [b.get("name") for b in targeting["behaviors"]]
        lines.append(f"  🎯 Behaviors: {', '.join(names)}")

    if "custom_audiences" in targeting:
        lines.append(f"  👥 Custom audiences: {len(targeting['custom_audiences'])} used")

    if "publisher_platforms" in targeting:
        lines.append(f"  📱 Platforms: {', '.join(targeting['publisher_platforms'])}")

    return "\n".join(lines) if lines else "  (broad/automatic targeting)"


def fmt_creative(creative):
    """Extract everything about the creative — text, image, video."""
    if not creative:
        return "  (no creative data)"
    lines = []

    # Headlines and body text from various places
    title = creative.get("title")
    body = creative.get("body")

    # Sometimes copy lives inside object_story_spec
    spec = creative.get("object_story_spec", {})
    link_data = spec.get("link_data") or spec.get("video_data") or {}
    title = title or link_data.get("name") or link_data.get("title")
    body = body or link_data.get("message") or link_data.get("description")
    cta = link_data.get("call_to_action", {}).get("type")
    link = link_data.get("link")

    if title: lines.append(f"  📰 Headline: {title}")
    if body:
        body_short = body[:200] + ("..." if len(body) > 200 else "")
        lines.append(f"  ✍️  Copy: {body_short}")
    if cta: lines.append(f"  🔘 CTA: {cta}")
    if link: lines.append(f"  🔗 Link: {link}")

    # Media
    image_url = creative.get("image_url") or link_data.get("picture")
    video_id = creative.get("video_id") or link_data.get("video_id")
    thumbnail = creative.get("thumbnail_url")

    if image_url: lines.append(f"  🖼️  Image: {image_url}")
    if video_id: lines.append(f"  🎥 Video ID: {video_id}")
    if thumbnail and not image_url: lines.append(f"  🖼️  Thumbnail: {thumbnail}")

    # Hashtags from body
    if body:
        hashtags = [w for w in body.split() if w.startswith("#")]
        if hashtags:
            lines.append(f"  🏷️  Hashtags: {' '.join(hashtags)}")

    return "\n".join(lines) if lines else "  (no creative details)"


def fmt_performance(insights):
    """Performance metrics in a clean block."""
    if not insights:
        return "  (no performance data)"
    spend = float(insights.get("spend", 0))
    impressions = int(insights.get("impressions", 0))
    reach = int(insights.get("reach", 0))
    clicks = int(insights.get("clicks", 0))
    ctr = float(insights.get("ctr", 0))
    cpc = float(insights.get("cpc", 0))
    cpm = float(insights.get("cpm", 0))
    freq = float(insights.get("frequency", 0))

    lines = [
        f"  💰 Spend: ${spend:,.2f}",
        f"  👁️  Impressions: {impressions:,}  |  Reach: {reach:,}  |  Frequency: {freq:.2f}",
        f"  🖱️  Clicks: {clicks:,}  |  CTR: {ctr:.2f}%  |  CPC: ${cpc:.2f}  |  CPM: ${cpm:.2f}",
    ]

    # Conversion actions
    actions = insights.get("actions", [])
    if actions:
        lines.append("  🎯 Conversions:")
        for a in actions:
            lines.append(f"     • {a.get('action_type')}: {a.get('value')}")

    return "\n".join(lines)


def print_ad(ad, rank):
    print(f"\n{'═' * 70}")
    print(f"🥇 RANK #{rank}: {ad.get('name')}")
    print(f"{'═' * 70}")

    campaign = ad.get("campaign", {})
    adset = ad.get("adset", {})
    print(f"\n📋 CAMPAIGN")
    print(f"  Name: {campaign.get('name')}")
    print(f"  Objective: {campaign.get('objective')}")
    print(f"  Ad Set: {adset.get('name')}")
    print(f"  Optimization: {adset.get('optimization_goal')}")
    if adset.get("start_time"):
        print(f"  Period: {adset.get('start_time', '')[:10]} → {adset.get('end_time', 'ongoing')[:10] if adset.get('end_time') else 'ongoing'}")

    print(f"\n🎯 TARGETING")
    print(fmt_targeting(adset.get("targeting", {})))

    print(f"\n🎨 CREATIVE")
    print(fmt_creative(ad.get("creative", {})))

    print(f"\n📊 PERFORMANCE (lifetime)")
    insights = (ad.get("insights", {}).get("data") or [{}])[0]
    print(fmt_performance(insights))


if __name__ == "__main__":
    for label, acct_id in AD_ACCOUNTS:
        if not acct_id:
            print(f"⚠️  {label}: not set in .env\n")
            continue

        print("\n" + "█" * 70)
        print(f"  📊 {label} ({acct_id}) — TOP 2 ADS BY SPEND")
        print("█" * 70)

        top_ads = fetch_top_ads(acct_id, limit=2)
        if isinstance(top_ads, dict) and "error" in top_ads:
            print(f"❌ Error: {top_ads['error']}")
            continue
        if not top_ads:
            print("\nNo ads with spend found in this account.")
            continue

        for i, ad in enumerate(top_ads, 1):
            print_ad(ad, i)

    print()