import sqlite3, json
db = sqlite3.connect("ads_manager.db")
rows = db.execute(
    "SELECT id, payload FROM ad_proposals WHERE proposal_type='LAUNCH' ORDER BY id DESC LIMIT 3"
).fetchall()
for row in rows:
    if row[1]:
        print(f"--- ID {row[0]} ---")
        print(json.dumps(json.loads(row[1]), indent=2))
