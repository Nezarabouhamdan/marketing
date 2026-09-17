"""Vision-based smart post planner. Reads strategy + analyzes image to recommend everything."""
import os
import json
import base64
from pathlib import Path
from anthropic import Anthropic

claude = Anthropic()
CACHE_FILE = Path("daily_cache.json")
STRATEGY_FILE = Path("latest_strategy.json")


def load_strategy_context() -> str:
    """Pull the strategy + cache to give Claude context about what to push."""
    context = ""

    if STRATEGY_FILE.exists():
        with open(STRATEGY_FILE, "r", encoding="utf-8") as f:
            strat = json.load(f)
        # Strip HTML tags for a cleaner text context
        import re
        html = strat.get("strategy_html", "")
        clean = re.sub(r"<[^>]+>", " ", html).strip()
        context += "═══ THIS WEEK'S STRATEGY ═══\n" + clean[:2500] + "\n\n"

    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

        # Recent post performance — timing patterns
        report = cache.get("report", {})
        posts = report.get("posts", [])[:5]
        if posts:
            context += "═══ RECENT POST PERFORMANCE ═══\n"
            for p in posts:
                ts = p.get("timestamp", "")[:16].replace("T", " ")
                context += (
                    f"  [{p.get('media_type')}] {ts} "
                    f"→ {p.get('like_count', 0)} likes, {p.get('comments_count', 0)} comments\n"
                )
            context += "\n"

        # Competitor activity
        comps = cache.get("competitors", {}).get("competitors", [])
        valid = [c for c in comps if "error" not in c]
        if valid:
            context += "═══ COMPETITORS ═══\n"
            for c in valid[:4]:
                tp = c.get("top_post") or {}
                context += (
                    f"  @{c.get('username')}: {c.get('posts_per_week', '?')} posts/wk, "
                    f"top post: {tp.get('likes', 0)} likes\n"
                )
            context += "\n"

        # Hashtag trends
        tags = cache.get("monitor", {}).get("hashtags", [])
        valid_tags = [t for t in tags if "error" not in t]
        if valid_tags:
            context += "═══ TRENDING IN OUR NICHE ═══\n"
            for t in valid_tags[:3]:
                top = (t.get("posts") or [])[:2]
                avg_likes = sum(p.get("like_count", 0) for p in top) / max(len(top), 1)
                context += f"  #{t['hashtag']}: avg top-post likes = {avg_likes:.0f}\n"

    return context


def encode_image(image_path: str) -> tuple:
    """Read image file → (base64_string, media_type)."""
    path = Path(image_path)
    ext = path.suffix.lower().lstrip(".")
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp", "gif": "image/gif",
    }
    media_type = mime_map.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


_PROMPT_TEMPLATE = """You're looking at {image_desc} for Khales Group's Instagram.

Format: {format}
User notes: {notes}

CONTEXT FROM RECENT INTELLIGENCE:
{context}

ANALYZE the image(s) and produce a complete posting recommendation in this exact JSON structure (return ONLY the JSON, no markdown, no preamble):

{{
  "image_analysis": "1-2 sentence factual description of what you see (architecture style, materials, mood, key elements)",
  "tier_recommendation": "signature",
  "tier_reasoning": "1 sentence why this tier fits the image",
  "captions": [
    {{
      "style": "informative",
      "caption_en": "English caption text",
      "caption_ar": "النص العربي",
      "hashtags": ["#KhalesGroup", "#hashtag2"]
    }},
    {{
      "style": "emotional",
      "caption_en": "...",
      "caption_ar": "...",
      "hashtags": ["..."]
    }},
    {{
      "style": "minimalist",
      "caption_en": "...",
      "caption_ar": "...",
      "hashtags": ["..."]
    }}
  ],
  "recommended_post_time": "2026-05-08T19:00:00+04:00",
  "timing_reasoning": "1-2 sentences explaining why this date/time"
}}

CAPTION RULES:
- Never quote prices, costs, or timelines
- English first in caption_en, Arabic in caption_ar (separate fields)
- Total per caption under 2200 characters
- End each with a soft CTA: "Visit khales.ae" or "WhatsApp +971 55 129 9880"
- Hashtags: 8-15 per option, mix branded (#KhalesGroup), niche, broad
- Tier tones — signature: aspirational/elegant, elite: exclusive/understated luxury, general: warm/inviting
- Format awareness — CAROUSEL: "Swipe to see...", REELS: high energy opener

TIMING RULES:
- recommended_post_time must be in the FUTURE (after today 2026-05-07)
- Use +04:00 timezone (Asia/Dubai)
- Consider: evening (18:00-21:00) or morning (08:00-10:00) for HNW audience
- Weekdays for B2B content, weekends for lifestyle/aspirational
- Avoid posting same day/time as recent posts if possible

TIER values must be exactly one of: "general", "signature", "elite"
Return ONLY the JSON object."""


def analyze_and_recommend(
    image_paths: list,
    format: str = "IMAGE",
    user_notes: str = "",
) -> dict:
    """
    Claude looks at the image(s), reads strategy context, returns full recommendation.
    Works for IMAGE (single), CAROUSEL (multiple), REELS (pass thumbnail path).
    """
    strategy_context = load_strategy_context()

    content_blocks = []

    # Add images (up to 5 for carousel)
    added_images = 0
    for img_path in image_paths[:5]:
        try:
            data, mime = encode_image(img_path)
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
            added_images += 1
        except Exception as e:
            print(f"[smart_scheduler] skipping image {img_path}: {e}")

    if added_images == 0:
        return {"error": "No readable images found in the provided paths."}

    image_desc = (
        "an image" if added_images == 1
        else f"{added_images} images for a carousel post"
    )

    content_blocks.append({
        "type": "text",
        "text": _PROMPT_TEMPLATE.format(
            image_desc=image_desc,
            format=format,
            notes=user_notes or "(none)",
            context=strategy_context or "(no strategy context available yet — using defaults)",
        ),
    })

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            messages=[{"role": "user", "content": content_blocks}],
        )
        text = response.content[0].text.strip()
    except Exception as e:
        return {"error": f"Claude API error: {e}"}

    # Strip markdown fences if present
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        return {
            "error": f"Could not parse Claude response as JSON: {e}",
            "raw_response": text[:500],
        }

    # Validate/normalise tier
    valid_tiers = {"general", "signature", "elite"}
    tier = result.get("tier_recommendation", "general").lower()
    result["tier_recommendation"] = tier if tier in valid_tiers else "general"

    # Ensure captions list has 3 entries
    captions = result.get("captions", [])
    for cap in captions:
        cap.setdefault("style", "informative")
        cap.setdefault("caption_en", "")
        cap.setdefault("caption_ar", "")
        cap.setdefault("hashtags", [])

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python smart_scheduler.py <image_path> [format] [notes]")
        sys.exit(1)
    fmt = sys.argv[2] if len(sys.argv) > 2 else "IMAGE"
    notes = sys.argv[3] if len(sys.argv) > 3 else ""
    result = analyze_and_recommend([sys.argv[1]], format=fmt, user_notes=notes)
    print(json.dumps(result, indent=2, ensure_ascii=False))
