"""
Token refresh wizard. Run when token expires:
    python refresh.py

Streamlines the manual refresh into a single guided flow.
"""
import os
import sys
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
ENV_PATH = Path(".env")

REQUIRED_PERMISSIONS = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_metadata",
    "pages_manage_posts",
    "pages_messaging",
    "instagram_basic",
    "instagram_manage_insights",
    "instagram_manage_comments",
    "instagram_manage_messages",
    "instagram_content_publish",
    "business_management",
    "ads_read",
]


def banner(text):
    print()
    print("━" * 60)
    print(text)
    print("━" * 60)


def step(num, text):
    print(f"\n  {num}. {text}")


def update_env_file(updates: dict):
    """Update or insert keys in .env file, preserving other lines."""
    if not ENV_PATH.exists():
        print("❌ .env file not found in current directory")
        sys.exit(1)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen_keys = set()
    new_lines = []

    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen_keys.add(key)
                continue
            # Drop META_SHORT_TOKEN entirely - we don't need it after refresh
            if key == "META_SHORT_TOKEN":
                continue
        new_lines.append(line)

    # Add any keys that weren't in the file
    for key, value in updates.items():
        if key not in seen_keys:
            new_lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main():
    banner("🔄 KHALES AGENT — TOKEN REFRESH WIZARD")

    app_id = os.getenv("META_APP_ID")
    app_secret = os.getenv("META_APP_SECRET")
    page_id = os.getenv("META_PAGE_ID")

    missing = [k for k, v in [
        ("META_APP_ID", app_id),
        ("META_APP_SECRET", app_secret),
        ("META_PAGE_ID", page_id),
    ] if not v]
    if missing:
        print(f"\n❌ Missing in .env: {', '.join(missing)}")
        sys.exit(1)

    print("\n📋 Step 1 — Generate a fresh User Token from Meta:")
    step("1", "Open in your browser:")
    print("       https://developers.facebook.com/tools/explorer")
    step("2", "Top-right: Meta App = your Khales app · User/Page = User Token")
    step("3", "Confirm these 11 permissions are added:")
    for perm in REQUIRED_PERMISSIONS:
        print(f"       • {perm}")
    step("4", "Click 'Generate Access Token' → log in → grant access")
    step("5", "Make sure your Page is checked, then click 'Continue'")
    step("6", "Copy the token from the 'Access Token' field at the top")

    print()
    short_token = input("📋 Paste the new User Token here: ").strip()

    if not short_token or not short_token.startswith("EA"):
        print("\n❌ That doesn't look like a valid token (should start with 'EA').")
        sys.exit(1)

    banner("⚙️  Exchanging tokens...")

    # 1. Short → Long-lived User Token
    print("  1/3 Exchanging short-lived token for long-lived...")
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    })
    if r.status_code != 200:
        err = r.json().get("error", {}).get("message", "unknown error")
        print(f"\n❌ Token exchange failed: {err}")
        print("   Common causes:")
        print("   - Token already expired (took too long to paste)")
        print("   - App ID/Secret in .env doesn't match the app you used")
        print("   - Permissions weren't granted in the consent screen")
        sys.exit(1)
    long_user_token = r.json()["access_token"]
    print("      ✅ Got long-lived user token")

    # 2. Get Page Token
    print("  2/3 Getting Page access token...")
    r = requests.get(f"{GRAPH}/{page_id}", params={
        "fields": "access_token,name",
        "access_token": long_user_token,
    })
    if r.status_code != 200:
        err = r.json().get("error", {}).get("message", "unknown error")
        print(f"\n❌ Page token fetch failed: {err}")
        sys.exit(1)
    page_data = r.json()
    page_token = page_data["access_token"]
    page_name = page_data["name"]
    print(f"      ✅ Got Page token for: {page_name}")

    # 3. Get Instagram Business ID
    print("  3/3 Finding Instagram Business Account...")
    r = requests.get(f"{GRAPH}/{page_id}", params={
        "fields": "instagram_business_account",
        "access_token": page_token,
    })
    if r.status_code != 200:
        err = r.json().get("error", {}).get("message", "unknown error")
        print(f"\n❌ IG account fetch failed: {err}")
        sys.exit(1)
    data = r.json()
    if "instagram_business_account" not in data:
        print("\n❌ No Instagram Business Account linked to this Page.")
        print("   Check: Page Settings → Linked Accounts on Facebook")
        sys.exit(1)
    ig_id = data["instagram_business_account"]["id"]
    print(f"      ✅ IG Business Account ID: {ig_id}")

    # Update .env
    banner("📝 Updating .env file...")
    update_env_file({
        "META_PAGE_TOKEN": page_token,
        "META_IG_BUSINESS_ID": ig_id,
    })
    print("  ✅ Updated META_PAGE_TOKEN")
    print("  ✅ Updated META_IG_BUSINESS_ID")
    print("  ✅ Removed META_SHORT_TOKEN (no longer needed)")

    # Verify with token_manager
    banner("🔍 Verifying new token health...")
    try:
        # Reload .env to pick up new values
        from importlib import reload
        import dotenv
        reload(dotenv)
        dotenv.load_dotenv(override=True)

        from token_manager import get_token_info
        info = get_token_info(token=page_token)
        if info["is_valid"]:
            print(f"  ✅ Token is valid")
            print(f"  📅 Expires: {info.get('expires_at_human', 'never')}")
            days = info.get("days_until_expiry")
            if days is None:
                print(f"  ✨ This token doesn't expire — set it and forget it")
            else:
                print(f"  ✨ Days left: {days}")
        else:
            print(f"  ⚠️  Verification failed: {info.get('error_message')}")
    except Exception as e:
        print(f"  ⚠️  Couldn't verify: {e}")

    banner("🎉 ALL DONE!")
    print(f"\n  Refresh completed: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  You can now run: python app.py")
    print()


if __name__ == "__main__":
    main()