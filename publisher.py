"""Instagram Graph API publisher for scheduled posts."""
import os
import time
import json
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GRAPH = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_PAGE_TOKEN")
IG_ID = os.getenv("META_IG_BUSINESS_ID")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

logging.basicConfig(
    filename="publisher.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("publisher")


def _params(**extra):
    return {"access_token": TOKEN, **extra}


def _check_api_error(data: dict, context: str):
    if "error" in data:
        msg = data["error"].get("message", str(data["error"]))
        raise RuntimeError(f"{context}: {msg}")


# ── Container creation ────────────────────────────────────────────────────────

def upload_image_container(image_url: str, caption: str = None, is_carousel_item: bool = False) -> str:
    payload = {"image_url": image_url}
    if is_carousel_item:
        payload["is_carousel_item"] = "true"
    elif caption:
        payload["caption"] = caption

    r = requests.post(
        f"{GRAPH}/{IG_ID}/media",
        params=_params(),
        json=payload,
        timeout=30,
    )
    data = r.json()
    log.info("upload_image_container url=%s response=%s", image_url, data)
    _check_api_error(data, "upload_image_container")
    return data["id"]


def upload_video_container(video_url: str, caption: str, media_type: str = "REELS") -> str:
    payload = {
        "video_url": video_url,
        "caption": caption,
        "media_type": media_type,
    }
    r = requests.post(
        f"{GRAPH}/{IG_ID}/media",
        params=_params(),
        json=payload,
        timeout=30,
    )
    data = r.json()
    log.info("upload_video_container url=%s response=%s", video_url, data)
    _check_api_error(data, "upload_video_container")
    return data["id"]


def wait_for_container_ready(container_id: str, timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params=_params(fields="status_code"),
            timeout=15,
        )
        data = r.json()
        status = data.get("status_code", "")
        log.info("container %s status=%s", container_id, status)
        if status == "FINISHED":
            return True
        if status == "ERROR":
            return False
        time.sleep(5)
    log.warning("container %s timed out after %ss", container_id, timeout)
    return False


def create_carousel_container(child_ids: list, caption: str) -> str:
    payload = {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
    }
    r = requests.post(
        f"{GRAPH}/{IG_ID}/media",
        params=_params(),
        json=payload,
        timeout=30,
    )
    data = r.json()
    log.info("create_carousel_container children=%s response=%s", child_ids, data)
    _check_api_error(data, "create_carousel_container")
    return data["id"]


def publish_container(container_id: str) -> dict:
    r = requests.post(
        f"{GRAPH}/{IG_ID}/media_publish",
        params=_params(creation_id=container_id),
        timeout=30,
    )
    data = r.json()
    log.info("publish_container container=%s response=%s", container_id, data)
    _check_api_error(data, "publish_container")

    media_id = data["id"]

    # Fetch permalink
    r2 = requests.get(
        f"{GRAPH}/{media_id}",
        params=_params(fields="permalink"),
        timeout=15,
    )
    permalink = r2.json().get("permalink", "")
    return {"media_id": media_id, "permalink": permalink}


# ── Main entry point ──────────────────────────────────────────────────────────

def _upload_to_meta_cdn(local_path: str) -> str:
    """
    Upload a local image to Meta Ad Images CDN and return the public fbcdn.net URL.
    No ngrok needed — Meta hosts the image and gives back a public URL.
    """
    import os as _os
    ad_account = _os.getenv("META_AD_ACCOUNT_ID", "act_1580181188999758")
    if not ad_account.startswith("act_"):
        ad_account = f"act_{ad_account}"
    url = f"{GRAPH}/{ad_account}/adimages"
    with open(local_path, "rb") as fh:
        r = requests.post(url, files={"filename": fh},
                          data={"access_token": _os.getenv("META_USER_TOKEN") or _os.getenv("META_PAGE_TOKEN")},
                          timeout=60)
    data = r.json()
    log.info("_upload_to_meta_cdn response=%s", data)
    if "error" in data:
        raise RuntimeError(f"Meta CDN upload failed: {data['error'].get('message', data['error'])}")
    images = data.get("images", {})
    for key in images:
        cdn_url = images[key].get("url", "")
        if cdn_url:
            return cdn_url
    raise RuntimeError("Meta CDN upload returned no URL")


def _file_url(filename: str) -> str:
    """
    Get a publicly-reachable URL for a post image.
    Always uploads to Meta CDN (fbcdn.net) — avoids ngrok interstitial issues.
    """
    local = Path("uploads") / filename
    if not local.exists():
        local = Path(filename)
    if not local.exists():
        raise RuntimeError(f"Image not found locally: {filename}")
    return _upload_to_meta_cdn(str(local))


def publish_post(post_id: int) -> dict:
    """Main entry point. Loads post, publishes, updates DB. Returns result dict."""
    from posts_db import (
        get_post, mark_publishing, mark_published, mark_failed
    )

    post = get_post(post_id)
    if not post:
        raise ValueError(f"Post {post_id} not found")

    # Idempotency: only publish SCHEDULED (or FAILED for retry) posts
    if post["status"] not in ("SCHEDULED", "FAILED"):
        log.warning("publish_post skipped: post %s is %s", post_id, post["status"])
        return {"skipped": True, "status": post["status"]}

    mark_publishing(post_id)
    log.info("Publishing post %s format=%s", post_id, post["format"])

    try:
        media_paths = post["media_paths"]
        if isinstance(media_paths, str):
            media_paths = json.loads(media_paths)

        hashtags = post.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = json.loads(hashtags)

        tag_string = " ".join(f"#{t.lstrip('#')}" for t in hashtags)
        full_caption = post["caption"]
        if tag_string:
            full_caption = f"{full_caption}\n\n{tag_string}"

        fmt = post["format"].upper()

        if fmt == "IMAGE":
            url = _file_url(media_paths[0])
            container_id = upload_image_container(url, caption=full_caption)
            result = publish_container(container_id)

        elif fmt == "CAROUSEL":
            child_ids = []
            for filename in media_paths:
                url = _file_url(filename)
                cid = upload_image_container(url, is_carousel_item=True)
                child_ids.append(cid)
            container_id = create_carousel_container(child_ids, full_caption)
            result = publish_container(container_id)

        elif fmt == "REELS":
            url = _file_url(media_paths[0])
            container_id = upload_video_container(url, full_caption, "REELS")
            ready = wait_for_container_ready(container_id, timeout=120)
            if not ready:
                raise RuntimeError("Video container did not reach FINISHED state within 120s")
            result = publish_container(container_id)

        else:
            raise ValueError(f"Unknown format: {fmt}")

        mark_published(post_id, result["media_id"], result["permalink"])
        log.info("Post %s published: media_id=%s permalink=%s", post_id, result["media_id"], result["permalink"])
        return {"published": True, "media_id": result["media_id"], "permalink": result["permalink"]}

    except Exception as e:
        log.error("Post %s failed: %s", post_id, e, exc_info=True)
        mark_failed(post_id, e)
        return {"published": False, "error": str(e)}
