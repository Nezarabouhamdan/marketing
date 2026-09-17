import os
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("META_APP_ID")
APP_SECRET = os.getenv("META_APP_SECRET")
SHORT_TOKEN = os.getenv("META_SHORT_TOKEN")
PAGE_ID = os.getenv("META_PAGE_ID")

GRAPH = "https://graph.facebook.com/v21.0"


def check_env():
    """Make sure all required env vars are set."""
    missing = []
    for name, val in [
        ("META_APP_ID", APP_ID),
        ("META_APP_SECRET", APP_SECRET),
        ("META_SHORT_TOKEN", SHORT_TOKEN),
        ("META_PAGE_ID", PAGE_ID),
    ]:
        if not val:
            missing.append(name)
    if missing:
        print(f"❌ Missing in .env: {', '.join(missing)}")
        exit(1)


def get_long_lived_user_token():
    """Convert 1-hour token to 60-day token."""
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "fb_exchange_token": SHORT_TOKEN,
    })
    if r.status_code != 200:
        print(f"❌ Token exchange failed: {r.json()}")
        exit(1)
    return r.json()["access_token"]


def get_page_token(user_token):
    """Get the never-expiring Page token."""
    r = requests.get(f"{GRAPH}/{PAGE_ID}", params={
        "fields": "access_token,name",
        "access_token": user_token,
    })
    if r.status_code != 200:
        print(f"❌ Page token fetch failed: {r.json()}")
        exit(1)
    data = r.json()
    return data["access_token"], data["name"]


def get_instagram_business_id(page_token):
    """Find the IG Business Account linked to the Page."""
    r = requests.get(f"{GRAPH}/{PAGE_ID}", params={
        "fields": "instagram_business_account",
        "access_token": page_token,
    })
    if r.status_code != 200:
        print(f"❌ IG account fetch failed: {r.json()}")
        exit(1)
    data = r.json()
    if "instagram_business_account" not in data:
        print("❌ No Instagram Business Account linked to this Page.")
        print("   Go to Page Settings → Linked Accounts to verify the link.")
        exit(1)
    return data["instagram_business_account"]["id"]


if __name__ == "__main__":
    check_env()

    print("Step 1/3: Exchanging short-lived token for long-lived...")
    user_token = get_long_lived_user_token()
    print("✅ Got long-lived user token\n")

    print("Step 2/3: Getting Page access token...")
    page_token, page_name = get_page_token(user_token)
    print(f"✅ Got Page token for: {page_name}\n")

    print("Step 3/3: Finding Instagram Business Account...")
    ig_id = get_instagram_business_id(page_token)
    print(f"✅ Instagram Business Account ID: {ig_id}\n")

    print("=" * 60)
    print("ADD THESE TO YOUR .env FILE:")
    print("=" * 60)
    print(f"META_PAGE_TOKEN={page_token}")
    print(f"META_IG_BUSINESS_ID={ig_id}")
    print(f"META_USER_TOKEN={user_token}")

    print()
    print("Then you can DELETE the META_SHORT_TOKEN line — no longer needed.")