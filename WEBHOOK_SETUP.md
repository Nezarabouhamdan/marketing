# Webhook Setup Checklist

## Prerequisites
- [ ] App is running: `python app.py`
- [ ] ngrok installed: https://ngrok.com/download
- [ ] `.env` has `META_WEBHOOK_VERIFY_TOKEN` set (any random string, e.g. `khales_webhook_2024`)

---

## Step 1 — Expose the app with ngrok

```bash
ngrok http 5000
```

Copy the HTTPS URL shown (e.g. `https://abc123.ngrok.io`). It changes every restart unless you have a paid ngrok account.

---

## Step 2 — Configure webhook in Meta App Dashboard

1. Go to [Meta for Developers](https://developers.facebook.com) → Your App
2. Left sidebar → **Webhooks**
3. Click **Add Subscription** next to **Instagram**
4. Fill in:
   - **Callback URL**: `https://abc123.ngrok.io/webhook`
   - **Verify Token**: same value as `META_WEBHOOK_VERIFY_TOKEN` in your `.env`
5. Click **Verify and Save**
6. Your Flask app will receive a GET request and respond with the challenge — the console should log `Webhook verified`

---

## Step 3 — Subscribe to fields

After verification, tick these subscription fields:
- [x] `messages`
- [x] `messaging_postbacks`
- [x] `comments`

Click **Save**.

---

## Step 4 — Link the Instagram account to the app

1. Left sidebar → **Instagram** product settings
2. Under **Instagram Accounts**, click **Add Account** and select your Khales IG page
3. Confirm the page is subscribed to the app

---

## Step 5 — Test

### CLI test (no live webhook needed)
```bash
python bot.py --test "السلام عليكم"
python bot.py --test "what are your hours?"
python bot.py --test "كم سعر تصميم شقة؟"
python bot.py --test "where is your dubai office?"
python bot.py --test "what services do you offer?"
```

### Live test checklist
- [ ] Send a DM to your Khales IG account → bot replies within 5 seconds
- [ ] Comment on a Khales post → bot replies to the comment
- [ ] Open dashboard → **💬 Inbox** tab shows the conversation
- [ ] Send a pricing question → message appears in **Flagged** section with red badge
- [ ] Send 4+ messages in 5 min from same account → 4th message gets no reply (rate limit)
- [ ] Trigger the same webhook event twice → only one reply sent (idempotency)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Meta says "Callback URL failed" | Check ngrok is running and URL is correct |
| Bot doesn't reply to DMs | Check `instagram_manage_messages` scope is on token |
| Bot doesn't reply to comments | Check `instagram_manage_comments` scope is on token |
| `403 Forbidden` on webhook verify | `META_WEBHOOK_VERIFY_TOKEN` in `.env` doesn't match what you entered in Meta dashboard |
| Replies appear twice | Idempotency guard active — check `meta_message_id` is being set |

---

## Updating the ngrok URL

Every time ngrok restarts with a free plan, the URL changes. Repeat Steps 2–3 with the new URL.
For a stable URL, use a paid ngrok account with a reserved domain, or deploy the app to a server.
