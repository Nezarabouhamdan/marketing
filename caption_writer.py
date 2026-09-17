"""Claude-powered caption generator for scheduled posts."""
import json
from anthropic import Anthropic
from knowledge import BUSINESS

claude = Anthropic()

_SYSTEM = """You are a bilingual social media copywriter for Khales Group (مجموعة خالص), a UAE luxury engineering & construction firm.

Generate exactly 3 distinct caption options as a JSON array. Each element must have:
{
  "caption_en": "English caption text",
  "caption_ar": "Arabic caption text (right-to-left, natural Arabic)",
  "hashtags": ["#KhalesGroup", "#luxuryvilladubai", ...],
  "style": "informative" | "emotional" | "minimalist"
}

RULES — never break these:
1. NEVER quote any price, cost, AED amount, or budget.
2. NEVER promise a timeline or delivery date.
3. English first, then Arabic — separate them with a blank line. Both in the same caption field is fine for bilingual posts, but keep caption_en and caption_ar separate fields.
4. Hashtags: 8-15 tags per option. Mix: branded (#KhalesGroup, #Khales), niche (#luxuryvilladubai, #villadesign, #interiordesignuae), and broad (#dubaiarchitecture, #uaerealestate).
5. Tier tone:
   - general  → warm, welcoming, community feel
   - signature → aspirational, elegant, curated
   - elite     → exclusive, exceptional, discreet, UHNW language
6. Format awareness:
   - IMAGE → single strong moment, caption can be short or medium
   - CAROUSEL → tease the journey ("Swipe to see...", "From concept to reality...")
   - REELS → high energy opener, dynamic language
7. End with a soft CTA: "Visit khales.ae" or "WhatsApp +971 55 129 9880" or "Book your consultation at khales.ae"
8. Each option must feel genuinely distinct (informative, emotional, minimalist — not just rewording).
9. Total caption length: under 2200 characters.
10. Return ONLY the JSON array — no markdown, no explanation."""


def generate_captions(
    image_description: str,
    format: str = "IMAGE",
    tier: str = "general",
    language: str = "both",
) -> list:
    prompt = f"""Content description: {image_description}
Format: {format}
Tier: {tier}
Business: {BUSINESS['name_en']} — {', '.join(BUSINESS['services'])}
Website: {BUSINESS['website']} | WhatsApp: {BUSINESS['whatsapp_24_7']}

Generate 3 caption options."""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]

        captions = json.loads(raw)

        # Validate and sanitise each item
        result = []
        for item in captions[:3]:
            result.append({
                "caption_en": item.get("caption_en", ""),
                "caption_ar": item.get("caption_ar", ""),
                "hashtags": item.get("hashtags", []),
                "style": item.get("style", "informative"),
            })
        return result

    except Exception as e:
        print(f"[caption_writer] error: {e}")
        # Return a safe fallback so the UI doesn't break
        return [{
            "caption_en": f"Khales Group — Excellence in engineering & design.\n\nVisit {BUSINESS['website']}",
            "caption_ar": f"مجموعة خالص — التميز في الهندسة والتصميم.\n\nزورونا على {BUSINESS['website']}",
            "hashtags": ["#KhalesGroup", "#luxuryvilladubai", "#dubaiarchitecture"],
            "style": "informative",
        }]


if __name__ == "__main__":
    import sys
    desc = " ".join(sys.argv[1:]) or "A stunning luxury villa exterior at golden hour, Dubai skyline in the background"
    results = generate_captions(desc, format="IMAGE", tier="signature")
    for i, c in enumerate(results, 1):
        print(f"\n--- Option {i} ({c['style']}) ---")
        print("EN:", c["caption_en"][:200])
        print("AR:", c["caption_ar"][:200])
        print("Tags:", " ".join(c["hashtags"]))
