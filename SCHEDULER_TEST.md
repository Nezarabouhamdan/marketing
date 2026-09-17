# Post Scheduler — Testing Checklist

## Prerequisites
- [ ] `.env` has `PUBLIC_BASE_URL=https://your-ngrok-url.ngrok-free.dev`
- [ ] ngrok running: `ngrok http 5000`
- [ ] App running: `python app.py`

---

## Step 1 — Database Init
```powershell
python posts_db.py
```
Expected: `[OK] posts_db initialised at ...scheduled_posts.db`

---

## Step 2 — File Upload
1. Open dashboard → **📤 Schedule Posts** tab
2. Click the file upload area, pick a JPG image
3. Click **Upload Files**
Expected: filename appears in the "Uploaded files" list

Also verify the file is reachable by Meta:
```
https://YOUR-NGROK/uploads/your_file.jpg
```
Open that URL in your browser — image must load (not 404).

---

## Step 3 — Caption Generation
1. Type a description: *"Luxury villa exterior, marble facade, Dubai skyline at sunset"*
2. Choose Format = IMAGE, Tier = Signature
3. Click **Generate Captions with Claude** (~10 sec)

Expected: 3 caption cards appear, each with English text, Arabic text, and hashtags.
Each card has a distinct style (informative / emotional / minimalist).

---

## Step 4 — Create Draft
1. Click a caption card to select it
2. Optionally edit the caption or hashtags
3. Pick a date/time in the future
4. Click **Save as Draft**

Expected: Post appears in **DRAFT** section with a yellow badge.

---

## Step 5 — Approve
1. Click **Approve** on the draft post

Expected: Post moves to **SCHEDULED** section with green badge.

---

## Step 6 — Auto-publish test
1. Set scheduled_for = 2 minutes from now
2. Approve the post
3. Wait 2 minutes (scheduler checks every minute)

Expected:
- Status briefly shows **PUBLISHING** (purple spinner)
- Then shows **PUBLISHED** (green tick) with a link to the IG post
- Check your Instagram — the post appears!

---

## Step 7 — Carousel test
1. Upload 3 images
2. Choose Format = CAROUSEL
3. Generate captions, approve, schedule
4. After publish: verify carousel with 3 slides on Instagram

---

## Step 8 — Reels test
1. Upload a `.mp4` video file
2. Choose Format = REELS
3. Generate captions, approve, schedule
4. After publish: check Instagram Reels tab (~30-60 sec for video processing)

---

## Step 9 — Manual publish
1. Create and approve a post scheduled far in the future
2. Click **Publish Now** button
3. Should publish immediately without waiting for scheduler

---

## Step 10 — Failure handling
1. Set `PUBLIC_BASE_URL` to an invalid URL in `.env`, restart app
2. Try to publish a post
3. Expected: status = **FAILED**, error message shows in dashboard
4. Restore correct URL, click **Retry** — post should publish

---

## Pricing / caption safety check
- [ ] Open `caption_writer.py` output — verify no AED/price/$ amounts in any caption
- [ ] Captions end with khales.ae or WhatsApp CTA

---

## Idempotency check
- [ ] Call `python -c "from publisher import publish_post; print(publish_post(1))"` twice on the same post_id
- [ ] Second call returns `{"skipped": True, "status": "PUBLISHED"}` — no duplicate post on IG

---

## Log files
Check for errors after any publish attempt:
```powershell
Get-Content publisher.log -Tail 30
Get-Content scheduler.log -Tail 30
```
