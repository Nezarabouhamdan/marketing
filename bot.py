"""Core bot logic: language detection, intent classification, reply generation, sending."""
import os
import json
import sys
import argparse
import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from knowledge import BUSINESS

load_dotenv()

claude = Anthropic()
GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
PAGE_ID = os.getenv("META_PAGE_ID")
IG_ID = os.getenv("META_IG_BUSINESS_ID")


# ── Language detection ────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """Return 'ar' if >30% of letters are Arabic Unicode, else 'en'."""
    if not text:
        return "en"
    arabic_count = sum(1 for c in text if "؀" <= c <= "ۿ")
    letter_count = sum(1 for c in text if c.isalpha())
    if letter_count == 0:
        return "en"
    return "ar" if arabic_count / letter_count > 0.30 else "en"


# ── Intent classification ─────────────────────────────────────────────────────

_INTENT_SYSTEM = """You classify Instagram messages for a UAE luxury engineering firm.

Return ONLY valid JSON with exactly these keys:
{"category": "...", "needs_human": true/false, "confidence": 0.0-1.0}

Categories:
- greeting          : hello, hi, salam, just saying hi
- faq_hours         : asking about working hours / when are you open
- faq_location      : asking about address / location / which emirate / office
- faq_services      : asking what services they offer
- faq_contact       : asking for email / phone / how to reach them
- pricing_inquiry   : ANY mention of cost / price / budget / AED / quote / كم / سعر / تكلفة → needs_human=true
- project_request   : wants to start a project / proposal / consultation request → needs_human=true
- complaint         : expressing dissatisfaction → needs_human=true
- other             : anything else → needs_human=true

Rules:
- pricing_inquiry ALWAYS needs_human=true — do not deviate
- project_request ALWAYS needs_human=true
- complaint ALWAYS needs_human=true
- Return ONLY JSON, no explanation, no markdown"""


def classify_intent(text: str, lang: str) -> dict:
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=300,
            system=_INTENT_SYSTEM,
            messages=[{"role": "user", "content": f"[lang={lang}] {text}"}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        result.setdefault("needs_human", False)
        result.setdefault("confidence", 1.0)
        return result
    except Exception as e:
        print(f"[classify_intent] error: {e}")
        return {"category": "other", "needs_human": True, "confidence": 0.0}


# ── Reply generation ──────────────────────────────────────────────────────────

def _offices_text():
    lines = []
    for o in BUSINESS["offices"]:
        status = o.get("status", "")
        if status:
            lines.append(f"• {o['name']}: {o['address']} ({status})")
        else:
            lines.append(f"• {o['name']}: {o['address']} — {o.get('phone', '')}")
    return "\n".join(lines)


def _services_text():
    return ", ".join(BUSINESS["services"])


_REPLY_SYSTEM = """You are the friendly customer service bot for {name_en} ({name_ar}), a UAE luxury engineering & construction firm.

Rules (NEVER break these):
1. NEVER quote any price, cost, or budget estimate.
2. NEVER promise any timeline or delivery date.
3. For pricing / project / complaint enquiries: acknowledge warmly then direct to WhatsApp {wa}.
4. Keep replies under 400 characters — this is an Instagram DM.
5. Reply in the SAME language as the customer ({lang_label}).
6. Be warm, professional, and brief.
7. Always sign off with the WhatsApp number when escalating.

Business info you may use:
- Hours: {hours}
- Offices: {offices}
- Services: {services}
- WhatsApp: {wa}
- Website: {website}
- Email: {email}"""


def generate_reply(text: str, intent: dict, lang: str) -> str:
    category = intent.get("category", "other")
    needs_human = intent.get("needs_human", True)
    lang_label = "Arabic" if lang == "ar" else "English"
    wa = BUSINESS["whatsapp_24_7"]

    system = _REPLY_SYSTEM.format(
        name_en=BUSINESS["name_en"],
        name_ar=BUSINESS["name_ar"],
        wa=wa,
        hours=BUSINESS["working_hours"],
        offices=_offices_text(),
        services=_services_text(),
        website=BUSINESS["website"],
        email=BUSINESS["email"],
        lang_label=lang_label,
    )

    if needs_human:
        if lang == "ar":
            prompt = (
                f"Customer sent (Arabic): \"{text}\"\n"
                f"Category: {category}\n"
                "Write a warm acknowledgment in Arabic and escalate to WhatsApp. Under 400 chars."
            )
        else:
            prompt = (
                f"Customer sent: \"{text}\"\n"
                f"Category: {category}\n"
                "Write a warm acknowledgment in English and escalate to WhatsApp. Under 400 chars."
            )
    else:
        prompts = {
            "greeting": f"Customer says hi in {lang_label}. Reply with a short, warm greeting. Under 400 chars.",
            "faq_hours": f"Customer asks about hours in {lang_label}. Answer using the business hours info. Under 400 chars.",
            "faq_location": f"Customer asks about location/office in {lang_label}. Mention the relevant UAE offices. Under 400 chars.",
            "faq_services": f"Customer asks about services in {lang_label}. List the services naturally. Under 400 chars.",
            "faq_contact": f"Customer asks how to contact in {lang_label}. Give phone, email, WhatsApp. Under 400 chars.",
        }
        prompt = prompts.get(category, f"Customer says: \"{text}\". Reply helpfully in {lang_label}. Under 400 chars.")

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        reply = resp.content[0].text.strip()
        # Hard cap — IG DM limit safety
        if len(reply) > 1000:
            reply = reply[:997] + "..."
        return reply
    except Exception as e:
        print(f"[generate_reply] error: {e}")
        if lang == "ar":
            return f"شكراً لتواصلك مع خالص. للمساعدة يرجى التواصل عبر واتساب: {wa}"
        return f"Thank you for contacting Khales. For assistance please WhatsApp us: {wa}"


# ── Meta API senders ──────────────────────────────────────────────────────────

def send_dm_reply(recipient_id: str, text: str) -> dict:
    url = f"{GRAPH}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    r = requests.post(url, params={"access_token": TOKEN}, json=payload, timeout=10)
    result = r.json()
    if "error" in result:
        print(f"[send_dm_reply] Meta error: {result['error']}")
    return result


def send_comment_reply(comment_id: str, text: str) -> dict:
    url = f"{GRAPH}/{comment_id}/replies"
    r = requests.post(url, params={"access_token": TOKEN}, json={"message": text}, timeout=10)
    result = r.json()
    if "error" in result:
        print(f"[send_comment_reply] Meta error: {result['error']}")
    return result


# ── Event handlers ────────────────────────────────────────────────────────────

def handle_dm_event(event: dict):
    """Process a single messaging event from Meta webhook."""
    from bot_db import log_dm, check_rate_limit

    sender = event.get("sender", {})
    sender_id = sender.get("id", "")
    msg_obj = event.get("message", {})
    text = msg_obj.get("text", "").strip()
    meta_msg_id = msg_obj.get("mid", "")

    # Skip echos (page sending to itself)
    if not text or sender_id == PAGE_ID or sender_id == IG_ID:
        return

    # Idempotency: skip if already processed
    if not log_dm(sender_id, None, text, "inbound", meta_message_id=meta_msg_id):
        print(f"[handle_dm] duplicate mid={meta_msg_id}, skipping")
        return

    # Rate limit check
    if not check_rate_limit(sender_id):
        print(f"[handle_dm] rate limit hit for user {sender_id}")
        return

    lang = detect_language(text)
    intent = classify_intent(text, lang)
    reply = generate_reply(text, intent, lang)

    result = send_dm_reply(sender_id, reply)
    success = "error" not in result

    # Update the inbound log row with the reply
    log_dm(
        sender_id, None, text, "inbound",
        reply_text=reply if success else None,
        lang=lang,
        needs_human=intent.get("needs_human", False),
        meta_message_id=meta_msg_id,
    )
    # Log outbound
    if success:
        log_dm(sender_id, None, reply, "outbound", lang=lang)

    print(f"[handle_dm] user={sender_id} lang={lang} cat={intent.get('category')} needs_human={intent.get('needs_human')}")


def handle_comment_event(value: dict):
    """Process a comment change event from Meta webhook."""
    from bot_db import log_comment, check_rate_limit

    comment_id = value.get("id", "")
    text = (value.get("text") or "").strip()
    post_id = value.get("media", {}).get("id", "") or value.get("post_id", "")
    from_obj = value.get("from", {})
    user_id = from_obj.get("id", "")
    username = from_obj.get("username", "") or from_obj.get("name", "")

    if not text or not comment_id:
        return

    # Skip own comments (page replying to itself)
    if user_id == PAGE_ID or user_id == IG_ID:
        return

    # Idempotency
    if not log_comment(post_id, user_id, username, text, meta_comment_id=comment_id):
        print(f"[handle_comment] duplicate comment_id={comment_id}, skipping")
        return

    # Rate limit per commenter
    if not check_rate_limit(user_id):
        print(f"[handle_comment] rate limit hit for user {user_id}")
        return

    lang = detect_language(text)
    intent = classify_intent(text, lang)
    reply = generate_reply(text, intent, lang)

    result = send_comment_reply(comment_id, reply)
    success = "error" not in result

    log_comment(
        post_id, user_id, username, text,
        reply=reply if success else None,
        lang=lang,
        needs_human=intent.get("needs_human", False),
        meta_comment_id=comment_id,
    )

    print(f"[handle_comment] user={username} lang={lang} cat={intent.get('category')} needs_human={intent.get('needs_human')}")


# ── CLI test harness ──────────────────────────────────────────────────────────

def _cli_test(text: str):
    lang = detect_language(text)
    intent = classify_intent(text, lang)
    reply = generate_reply(text, intent, lang)

    print(f"\n{'='*60}")
    print(f"Input   : {text}")
    print(f"Language: {lang}")
    print(f"Intent  : {intent}")
    print(f"Reply   : {reply}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    from bot_db import init_db
    init_db()

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=str, help="Test message to classify and reply to")
    args = parser.parse_args()

    if args.test:
        _cli_test(args.test)
    else:
        print("Usage: python bot.py --test \"your message here\"")
