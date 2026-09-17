"""SEO Brain — keywords, blogs, basic page checks for www.khales.ae"""
import json, re, requests
from anthropic import Anthropic

claude = Anthropic()
SITE = "https://www.khales.ae"
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}

SYSTEM_PROMPT = """You are an SEO advisor for Khales — UAE luxury engineering & construction firm targeting HNW investors 35-65 in UAE/GCC. Website: www.khales.ae

You help with:
- Finding keyword opportunities for UAE luxury real estate / construction
- Reviewing and improving blog content
- Suggesting new blog topics
- Basic page SEO checks (title, meta description, headings)

Be specific, practical, and business-focused. Match user's language (Arabic or English)."""

TOOLS = [
    {
        "name": "get_blog_posts",
        "description": "Get all blog posts on khales.ae with titles and URLs.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "read_blog_post",
        "description": "Read the full content of a specific blog post for review.",
        "input_schema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Blog post URL or path like /en/blog/slug"}
        }, "required": ["url"]}
    },
    {
        "name": "check_page_seo",
        "description": "Check basic SEO of any page: title, meta description, H1.",
        "input_schema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Page URL or path"}
        }, "required": ["url"]}
    },
    {
        "name": "suggest_keywords",
        "description": "Suggest target keywords for Khales based on their services and UAE market.",
        "input_schema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "Specific topic or service to find keywords for (optional)"}
        }}
    },
    {
        "name": "suggest_blog_topics",
        "description": "Suggest new blog post ideas that would rank well for UAE luxury construction audience.",
        "input_schema": {"type": "object", "properties": {
            "count": {"type": "integer", "description": "Number of ideas (default 8)"}
        }}
    },
]


def _fetch(url):
    try:
        if url.startswith("/"): url = SITE + url
        r = requests.get(url, headers=HEADERS, timeout=12)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


def _execute_tool(name, inputs):
    try:
        if name == "get_blog_posts":
            html = _fetch(f"{SITE}/en/blog")
            if not html: return {"error": "Could not fetch blog"}
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            posts, seen = [], set()
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if "/en/blog/" in h and h not in seen and len(h) > 10:
                    seen.add(h)
                    t = a.get_text().strip()
                    posts.append({"url": SITE + h if h.startswith("/") else h, "title": t[:100] if t else h.split("/")[-1].replace("-"," ").title()})
            return {"total": len(posts), "posts": posts}

        elif name == "read_blog_post":
            url = inputs["url"]
            if not url.startswith("http"): url = SITE + ("" if url.startswith("/") else "/") + url
            html = _fetch(url)
            if not html: return {"error": f"Could not fetch {url}"}
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.find("title") or {}).get_text("").strip()
            h1s = [h.get_text().strip() for h in soup.find_all("h1")]
            h2s = [h.get_text().strip() for h in soup.find_all("h2")]
            main = soup.find("article") or soup.find("main") or soup.find("body")
            content = main.get_text(" ", strip=True)[:4000] if main else ""
            words = len(content.split())
            return {"url": url, "title": title, "h1": h1s, "h2": h2s[:6], "word_count": words, "content": content[:2000]}

        elif name == "check_page_seo":
            url = inputs["url"]
            if not url.startswith("http"): url = SITE + ("" if url.startswith("/") else "/") + url
            html = _fetch(url)
            if not html: return {"error": f"Could not fetch {url}"}
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.find("title") or {}).get_text("").strip()
            md = soup.find("meta", attrs={"name": "description"})
            meta = md.get("content","").strip() if md else ""
            h1s = [h.get_text().strip() for h in soup.find_all("h1")]
            issues = []
            if not title:           issues.append("❌ Missing title tag")
            elif len(title) < 40:   issues.append(f"⚠️ Title too short ({len(title)} chars)")
            elif len(title) > 70:   issues.append(f"⚠️ Title too long ({len(title)} chars)")
            if not meta:            issues.append("❌ Missing meta description")
            elif len(meta) < 80:    issues.append(f"⚠️ Meta description too short ({len(meta)} chars)")
            if not h1s:             issues.append("❌ No H1 tag")
            elif len(h1s) > 1:      issues.append(f"⚠️ Multiple H1 tags ({len(h1s)})")
            return {"url": url, "title": title, "title_length": len(title), "meta_description": meta, "meta_length": len(meta), "h1": h1s, "issues": issues, "score": max(0, 100 - len([i for i in issues if "❌" in i])*25 - len([i for i in issues if "⚠️" in i])*10)}

        elif name == "suggest_keywords":
            topic = inputs.get("topic", "luxury construction UAE")
            prompt = f"""Khales is a UAE luxury engineering & construction firm. Target audience: HNW investors 35-65, UAE/GCC.
Topic: {topic}

List 15 high-value SEO keywords they should target. Mix of:
- High intent (ready to hire/invest)
- Informational (research phase)
- Arabic keywords

Return JSON: [{{"keyword": "...", "arabic": "...", "intent": "high/info", "difficulty": "low/med/high", "why": "1 line"}}]
Only JSON."""
            resp = claude.messages.create(model="claude-sonnet-4-6", max_tokens=1500, messages=[{"role":"user","content":prompt}])
            text = re.sub(r"^```[a-z]*\n?|```$", "", resp.content[0].text.strip(), flags=re.MULTILINE)
            return {"topic": topic, "keywords": json.loads(text)}

        elif name == "suggest_blog_topics":
            count = inputs.get("count", 8)
            posts = _execute_tool("get_blog_posts", {}).get("posts", [])
            existing = [p["title"] for p in posts[:10]]
            prompt = f"""Khales: UAE luxury engineering & construction. Audience: HNW investors 35-65.
Existing blogs: {json.dumps(existing)}
Suggest {count} NEW blog topics. Must target real search queries, fill content gaps, attract HNW clients.
Return JSON: [{{"title": "...", "arabic_title": "...", "target_keyword": "...", "priority": "HIGH/MED", "why": "1 sentence"}}]
Only JSON."""
            resp = claude.messages.create(model="claude-sonnet-4-6", max_tokens=1500, messages=[{"role":"user","content":prompt}])
            text = re.sub(r"^```[a-z]*\n?|```$", "", resp.content[0].text.strip(), flags=re.MULTILINE)
            return {"topics": json.loads(text)}

        else:
            return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        return {"error": str(e)}


def process_seo_message(message, history):
    messages = list(history) + [{"role": "user", "content": message}]
    response = claude.messages.create(model="claude-sonnet-4-6", max_tokens=3000, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)

    while response.stop_reason == "tool_use":
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        tool_results = []
        for tu in tool_uses:
            result = _execute_tool(tu.name, tu.input)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(result, ensure_ascii=False)})
        messages = messages + [{"role": "assistant", "content": response.content}, {"role": "user", "content": tool_results}]
        response = claude.messages.create(model="claude-sonnet-4-6", max_tokens=2000, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)

    return {"content": "".join(b.text for b in response.content if hasattr(b, "text")), "data": None, "data_type": None}
