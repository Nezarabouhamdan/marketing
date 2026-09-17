"""
Token health monitoring and auto-refresh utilities.
Checks token validity before API calls and alerts on issues.
"""
import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"


class TokenError(Exception):
    """Raised when token is invalid or expired."""
    pass


def get_token_info(token=None, app_id=None, app_secret=None):
    """
    Calls Meta's debug_token endpoint to check token health.
    Returns dict with: is_valid, expires_at, scopes, error_message
    """
    token = token or os.getenv("META_PAGE_TOKEN")
    app_id = app_id or os.getenv("META_APP_ID")
    app_secret = app_secret or os.getenv("META_APP_SECRET")

    if not token:
        return {
            "is_valid": False,
            "error_message": "No token in .env",
            "expires_at": None,
            "scopes": [],
        }

    # Meta requires an "app access token" (app_id|app_secret) to debug a token
    app_token = f"{app_id}|{app_secret}"

    try:
        r = requests.get(
            f"{GRAPH}/debug_token",
            params={"input_token": token, "access_token": app_token},
            timeout=10,
        )
        data = r.json().get("data", {})

        is_valid = data.get("is_valid", False)
        expires_at = data.get("expires_at", 0)  # 0 = never expires
        scopes = data.get("scopes", [])
        error = data.get("error", {}).get("message")

        return {
            "is_valid": is_valid,
            "expires_at": expires_at,
            "expires_at_human": (
                "never" if expires_at == 0
                else datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M")
            ),
            "scopes": scopes,
            "error_message": error,
            "days_until_expiry": (
                None if expires_at == 0
                else max(0, (expires_at - int(time.time())) // 86400)
            ),
        }
    except Exception as e:
        return {
            "is_valid": False,
            "error_message": f"Could not check token: {e}",
            "expires_at": None,
            "scopes": [],
        }


def assert_token_healthy():
    """
    Raises TokenError with a clear message if token is unhealthy.
    Call this before any Meta API operations.
    """
    info = get_token_info()

    if not info["is_valid"]:
        msg = info.get("error_message") or "Token is invalid"
        raise TokenError(
            f"❌ Page token is not valid: {msg}\n"
            f"   Run: python setup.py\n"
            f"   (See README for token refresh steps)"
        )

    days_left = info.get("days_until_expiry")
    if days_left is not None and days_left < 7:
        # Just a warning, don't fail
        print(f"⚠️  Token expires in {days_left} days ({info['expires_at_human']})")
        print(f"    Run python setup.py soon to refresh.")

    return info


def print_token_status():
    """Pretty-print current token status. Run this whenever you want a health check."""
    info = get_token_info()

    print("=" * 50)
    print("🔑 TOKEN HEALTH CHECK")
    print("=" * 50)

    if info["is_valid"]:
        print(f"✅ Status:    Valid")
        print(f"📅 Expires:   {info.get('expires_at_human', 'unknown')}")
        days = info.get("days_until_expiry")
        if days is not None:
            if days == 0:
                print(f"⚠️  Days left: <1 day — refresh ASAP")
            elif days < 7:
                print(f"⚠️  Days left: {days} — refresh soon")
            elif days < 30:
                print(f"📌 Days left: {days}")
            else:
                print(f"✨ Days left: {days} — healthy")
        scopes = info.get('scopes', [])
        print(f"🔐 Scopes:    {len(scopes)} permissions granted")
        for s in scopes:
            print(f"              • {s}")
    else:
        print(f"❌ Status:    INVALID")
        print(f"💬 Error:     {info.get('error_message', 'unknown')}")
        print(f"🔧 Fix:       Run `python setup.py` to refresh")

    print("=" * 50)


if __name__ == "__main__":
    print_token_status()