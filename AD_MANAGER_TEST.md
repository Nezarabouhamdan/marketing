# Ad Manager — Test Checklist

## Prerequisites
- App running: `python app.py`
- `daily_cache.json` exists (run `python scheduler.py` once to populate)
- `ads_manager.db` initialized (run `python ads_db.py` once)

---

## 1. DB sanity check
```
python ads_db.py
```
Expected: `[OK] ads_manager.db initialized`, 4 rules, 2 caps seeded

---

## 2. Simulation mode (default)
Confirm `.env` has:
```
ADS_WRITE_ENABLED=false
```

---

## 3. Generate proposals via UI
1. Open dashboard → click **🤖 Ad Manager** tab
2. Verify **SIMULATION** badge appears top-right (orange tag)
3. Click **⚡ Propose Now**
4. Wait ~10–20 s for Claude to respond
5. Proposals appear under **Pending Approval**

---

## 4. Generate proposals via API
```
curl -X POST http://localhost:5000/api/ad-manager/propose-now
```
Expected:
```json
{"proposed": 3, "ids": [1, 2, 3]}
```

---

## 5. View proposals
```
curl http://localhost:5000/api/ad-manager/proposals
```
Expected: `pending`, `recent`, `actions` arrays

---

## 6. Approve a proposal (simulated execute)
```
curl -X POST http://localhost:5000/api/ad-manager/proposal/1/approve
```
Expected:
```json
{"ok": true, "result": {"simulated": true, "would_post_to": "...", "payload": {...}}}
```
After approval, proposal moves from Pending → Executed in the UI.

---

## 7. Reject a proposal
```
curl -X POST http://localhost:5000/api/ad-manager/proposal/2/reject \
  -H "Content-Type: application/json" \
  -d '{"reason": "Budget not approved yet"}'
```
Expected: `{"status": "REJECTED"}`

---

## 8. Settings modal
1. Click **⚙️ Settings** in Ad Manager tab
2. Toggle any auto-rule ON
3. Change a spend cap value
4. Click **Save Settings**
5. Reopen Settings — verify values persisted

---

## 9. Settings API
```
curl http://localhost:5000/api/ad-manager/settings
```
Expected: `auto_rules` (4 entries), `spend_caps` (2 entries), `write_enabled: false`

```
curl -X POST http://localhost:5000/api/ad-manager/settings \
  -H "Content-Type: application/json" \
  -d '{"auto_rules":[{"rule_key":"pause_high_cpc","enabled":true,"threshold":4.0}],"spend_caps":[]}'
```
Expected: `{"saved": true}`

---

## 10. Scheduler integration
After confirming the above works:
1. Run `python scheduler.py` manually
2. Check output — should show `daily_refresh` + `ad_proposer` at 8:15 job registered
3. Or trigger manually:
```python
from scheduler import run_ad_proposer
run_ad_proposer()
```

---

## 11. Auto-refresh (60 s)
1. Open Ad Manager tab
2. Approve a proposal via curl while tab is open
3. Within 60 s the UI should refresh and show the update

---

## 12. Live mode (real API writes — DANGER)
Only enable after Meta App Review grants ads_management permission:
```
ADS_WRITE_ENABLED=true
```
- Dashboard shows red **LIVE MODE** badge
- All write operations hit real Meta API
- Test PAUSE on a $0-budget test ad first
