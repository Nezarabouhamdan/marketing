"""
Multi-platform content intelligence for Khales.
Fetches public profile data from Pinterest & LinkedIn,
then generates platform-specific content strategies using AI.
"""
import json
import os
import re
import time
import requests
from anthropic import Anthropic

claude = Anthropic()

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

PINTEREST_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": "application/json, text/javascript, */*, q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.pinterest.com/",
}


# ── Pinterest ─────────────────────────────────────────────────────────────────

def fetch_pinterest_profile(username: str = "khalesae") -> dict:
    """
    Fetch public Pinterest profile via the page HTML.
    Pinterest embeds a __PWS_INITIAL_PROPS__ JSON blob with profile data.
    """
    result = {
        "username": username,
        "url": f"https://www.pinterest.com/{username}/",
        "followers": 0,
        "following": 0,
        "pin_count": 0,
        "board_count": 0,
        "full_name": "",
        "about": "",
        "boards": [],
        "error": None,
    }

    try:
        r = requests.get(
            result["url"],
            headers=BROWSER_HEADERS,
            timeout=20,
        )
        if r.status_code != 200:
            result["error"] = f"Pinterest returned HTTP {r.status_code}"
            return result

        html = r.text

        # Pinterest embeds profile data in a script tag as JSON
        # Try __PWS_INITIAL_PROPS__ first
        for pattern in [
            r'__PWS_INITIAL_PROPS__\s*=\s*(\{.+?\});\s*</script>',
            r'__RELAY_STORE__\s*=\s*(\{.+?\});\s*</script>',
            r'"followerCount"\s*:\s*(\d+)',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                if pattern.endswith(r'(\d+)'):
                    result["followers"] = int(m.group(1))
                else:
                    try:
                        # Extract only what we need from the large blob
                        blob = m.group(1)
                        fc = re.search(r'"followerCount"\s*:\s*(\d+)', blob)
                        pc = re.search(r'"pinCount"\s*:\s*(\d+)', blob)
                        bc = re.search(r'"boardCount"\s*:\s*(\d+)', blob)
                        fn = re.search(r'"fullName"\s*:\s*"([^"]+)"', blob)
                        ab = re.search(r'"about"\s*:\s*"([^"]*)"', blob)
                        if fc: result["followers"]   = int(fc.group(1))
                        if pc: result["pin_count"]   = int(pc.group(1))
                        if bc: result["board_count"] = int(bc.group(1))
                        if fn: result["full_name"]   = fn.group(1)
                        if ab: result["about"]       = ab.group(1)[:200]
                    except Exception:
                        pass
                break

        # Extract board names from page HTML as fallback
        board_names = re.findall(r'"boardName"\s*:\s*"([^"]+)"', html)
        if not board_names:
            board_names = re.findall(r'data-board-name="([^"]+)"', html)
        if board_names:
            result["boards"] = [{"name": n} for n in dict.fromkeys(board_names)[:15]]

        if result["followers"] == 0 and result["pin_count"] == 0:
            result["error"] = "Could not extract profile stats from page (Pinterest may require login for detailed data)"

    except Exception as e:
        result["error"] = f"Pinterest fetch failed: {e}"

    return result


# ── LinkedIn ──────────────────────────────────────────────────────────────────

_KHALES_KNOWN = {
    "name": "Khales Engineering & Construction",
    "tagline": "Luxury villas and engineering excellence in the UAE. We design, build, and deliver high-end residential projects for discerning clients across the GCC.",
    "industry": "Construction & Real Estate",
    "website": "https://khales.ae",
}


def fetch_linkedin_company(slug: str = "khales-ae") -> dict:
    """
    Attempt to fetch public LinkedIn company page.
    LinkedIn aggressively blocks scraping — we extract what the HTML gives us
    and fall back gracefully.
    """
    result = {
        "slug": slug,
        "url": f"https://www.linkedin.com/company/{slug}/",
        "name": "",
        "tagline": "",
        "followers": 0,
        "employees": 0,
        "industry": "",
        "website": "",
        "recent_posts": [],
        "error": None,
    }

    try:
        r = requests.get(
            result["url"],
            headers=BROWSER_HEADERS,
            timeout=15,
            allow_redirects=True,
        )

        if r.status_code == 999:
            result["error"] = "LinkedIn is blocking automated access (HTTP 999)"
            return result
        if r.status_code != 200:
            result["error"] = f"LinkedIn returned HTTP {r.status_code}"
            return result

        html = r.text

        # Extract structured JSON-LD data
        ld_match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        if ld_match:
            try:
                ld = json.loads(ld_match.group(1))
                result["name"]      = ld.get("name", "")
                result["industry"]  = ld.get("industry", "")
                result["website"]   = ld.get("url", "")
                result["tagline"]   = ld.get("description", "")[:300]
            except Exception:
                pass

        # Follower count patterns
        for pat in [r'"followerCount":(\d+)', r'(\d[\d,]+)\s*followers']:
            m = re.search(pat, html)
            if m:
                result["followers"] = int(m.group(1).replace(",", ""))
                break

        # Employee count
        m = re.search(r'"staffCount":(\d+)', html)
        if m:
            result["employees"] = int(m.group(1))

        # Tagline / description if not found in LD
        if not result["tagline"]:
            m = re.search(r'"tagline":"([^"]{5,300})"', html)
            if m:
                result["tagline"] = m.group(1)

    except Exception as e:
        result["error"] = f"LinkedIn fetch failed: {e}"

    # Fill blanks with known Khales data — LinkedIn often blocks scraping
    for key, val in _KHALES_KNOWN.items():
        if not result.get(key):
            result[key] = val

    return result


# ── Platform Intelligence ─────────────────────────────────────────────────────

PLATFORM_KNOWLEDGE = {
    "pinterest": """PINTEREST BEST PRACTICES — Luxury Construction / Real Estate (UAE/GCC):

FORMAT PRIORITIES:
- Vertical images (2:3 ratio, 1000×1500px) = highest engagement
- Idea Pins (multi-slide video) get algorithmic boost since 2023
- Carousel boards get 3× saves vs single images

CONTENT THAT PERFORMS:
- Before/after transformation posts (highest save rate in home category)
- Zoomed-in material details: marble textures, wood grain, custom metalwork
- Mood boards: "Luxury Villa Aesthetic" boards get saved obsessively
- Step-by-step process: "How a custom majlis is built"
- Color palette inspiration boards tied to actual projects

BOARDS TO HAVE:
1. Villa Projects — complete project showcases
2. Interior Details — marble, wood, metal finishes
3. Majlis & Arabic Design — cultural design elements
4. Architecture & Structure — facades, staircases, ceilings
5. Color Palettes — from real projects
6. Inspiration / Client Vision boards

POSTING FREQUENCY: 5-15 pins/day (can bulk schedule via Tailwind)
BEST TIMES: Fri/Sat/Sun evenings 7-10pm UAE (global peak = 8pm ET)
LANGUAGE: English-dominant (international audience), Arabic for GCC boards
LINKS: Every pin should link to project page or WhatsApp inquiry
HASHTAGS: 5-10 per pin (niched: #luxuryvilla #interiordesignuae #modernmajlis)
ENGAGEMENT: Save rate > click rate — focus on inspirational content that gets saved""",

    "linkedin": """LINKEDIN BEST PRACTICES — B2B Luxury Construction / Engineering Firm (UAE):

AUDIENCE ON LINKEDIN:
- Property developers & real estate investors
- Architects and interior designers looking for execution partners
- HNW individuals researching contractors for villa builds
- Corporate clients (hotel chains, commercial fit-outs)

CONTENT FORMATS RANKED BY PERFORMANCE:
1. Document/Carousel posts (PDF-style slides) — highest saves + shares in construction
2. Native video (60-90 sec project tours) — 3× more reach than YouTube links
3. Long-form text posts (800-1200 chars) with one strong image
4. Short posts with project photo — for quick engagement
5. Polls — good for "What design style would you choose?" type engagement

CONTENT PILLARS:
- Project Case Studies: problem → solution → result with numbers (sqm, timeline, budget range)
- Behind the Scenes: craftsmen at work, materials being installed, quality control
- Thought Leadership: "What separates luxury construction from standard" articles
- Team & Expertise: Engineer/designer spotlights, certifications, awards
- Industry Trends: Biophilic design, smart home integration, sustainable luxury

POSTING FREQUENCY: 3-4× per week (consistency > volume on LinkedIn)
BEST TIMES: Tue/Wed/Thu 8-10am UAE (professional scrolling before meetings)
TONE: Professional but human — avoid corporate buzzwords, tell real project stories
HASHTAGS: 3-5 only (#luxuryconstruction #dubaiinteriors #villadesign #UAErealestate)
CTA: "DM us to discuss your project" or "Link in bio for portfolio" (no hard selling)
LANGUAGE: English primary (regional business language), Arabic for GCC investor posts""",
}


def generate_platform_strategy(platform: str, profile_data: dict, ig_context: str = "") -> dict:
    """
    Claude generates a complete, actionable content strategy for the platform.
    Incorporates real profile data and Instagram performance as cross-reference.
    """
    knowledge = PLATFORM_KNOWLEDGE.get(platform.lower(), "")
    profile_summary = json.dumps(profile_data, ensure_ascii=False, indent=2)

    has_error = bool(profile_data.get("error"))
    data_note = (
        "NOTE: Could not fetch live profile data (see error). "
        "Generate strategy based on platform best practices and Khales brand knowledge.\n\n"
        if has_error else ""
    )

    prompt = f"""You are the senior content strategist for Khales — UAE luxury engineering & construction firm.
Target: HNW individuals 35-65 in UAE/GCC + B2B architects/developers.
Brand pillars: precision craftsmanship, luxury materials, bilingual (Arabic/English) presence.

PLATFORM: {platform.upper()}

{data_note}CURRENT PROFILE DATA:
{profile_summary}

{f'INSTAGRAM PERFORMANCE CONTEXT (cross-reference for what content works):{chr(10)}{ig_context}' if ig_context else ''}

PLATFORM KNOWLEDGE:
{knowledge}

Based on the actual profile data and platform knowledge, generate a comprehensive strategy.
Return ONLY valid JSON — no markdown, no text outside JSON, all strings on single lines:

{{
  "current_state": {{
    "assessment": "honest 2-3 sentence assessment of where they are today on this platform",
    "strengths": ["strength 1", "strength 2"],
    "gaps": ["gap 1", "gap 2", "gap 3"]
  }},
  "strategy": {{
    "posting_frequency": "e.g. '3x per week'",
    "best_times": "e.g. 'Tue/Thu/Sat 9-11am UAE'",
    "primary_goal": "e.g. 'Drive project inquiry DMs from architects and developers'",
    "content_mix": [
      {{"type": "content type name", "percentage": 40, "why": "brief rationale"}},
      {{"type": "content type name", "percentage": 30, "why": "brief rationale"}},
      {{"type": "content type name", "percentage": 30, "why": "brief rationale"}}
    ]
  }},
  "post_ideas": [
    {{
      "title": "specific post concept",
      "format": "IMAGE|VIDEO|CAROUSEL|IDEA_PIN|DOCUMENT|REEL",
      "copy_ar": "Arabic caption — natural, luxury tone, under 200 chars",
      "copy_en": "English caption — professional luxury tone, under 200 chars",
      "visual_direction": "specific instructions: what to film/photograph, angles, lighting",
      "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
      "cta": "specific call to action"
    }}
  ],
  "quick_wins": [
    "immediate action 1 (can do this week)",
    "immediate action 2",
    "immediate action 3"
  ],
  "boards_or_topics": ["topic/board 1", "topic/board 2", "topic/board 3", "topic/board 4", "topic/board 5"]
}}

Generate exactly 6 post_ideas. Be hyper-specific to Khales UAE luxury construction — not generic advice.
IMPORTANT: Keep all string values SHORT. copy_ar and copy_en max 120 chars each. visual_direction max 100 chars. This keeps the JSON under token limits."""

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    return _parse_json_robust(raw)


def _parse_json_robust(raw: str) -> dict:
    """
    Extract and parse a JSON object from Claude output.
    Handles: code fences, literal newlines in strings, truncated output.
    """
    # Strip markdown code fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw)

    # Escape literal newlines/tabs inside JSON string values
    cleaned = []
    in_str  = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == '"' and (i == 0 or raw[i - 1] != "\\"):
            in_str = not in_str
            cleaned.append(c)
        elif in_str and c == "\n":
            cleaned.append("\\n")
        elif in_str and c == "\r":
            cleaned.append("\\r")
        elif in_str and c == "\t":
            cleaned.append("\\t")
        else:
            cleaned.append(c)
        i += 1
    raw = "".join(cleaned)

    # Find the outermost { ... } block — handles preamble text and truncation
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    depth    = 0
    in_str   = False
    escape   = False
    end      = -1
    for idx in range(start, len(raw)):
        c = raw[idx]
        if escape:
            escape = False
            continue
        if c == "\\" and in_str:
            escape = True
            continue
        if c == '"':
            in_str = not in_str
        if not in_str:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break

    if end == -1:
        # Truncated — close all open structures so json.loads can at least parse what we have
        raw = raw[start:]
        # Count unclosed braces/brackets and close them
        d_brace = 0
        d_brack = 0
        in_str  = False
        escape  = False
        for c in raw:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_str = not in_str
            if not in_str:
                if c == "{":
                    d_brace += 1
                elif c == "}":
                    d_brace -= 1
                elif c == "[":
                    d_brack += 1
                elif c == "]":
                    d_brack -= 1
        # Strip any dangling partial value at the end (after last complete comma-less key)
        raw = raw.rstrip().rstrip(",").rstrip()
        # If we're inside a string, close it
        if in_str:
            raw += '"'
        # Close open arrays then objects
        raw += "]" * max(d_brack, 0) + "}" * max(d_brace, 0)
    else:
        raw = raw[start:end]

    return json.loads(raw)
