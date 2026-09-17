import sqlite3
db = sqlite3.connect("ig_audit.db")
n = db.execute(
    "UPDATE ig_posts SET ai_verdict=NULL, ai_reason=NULL, audited_at=NULL WHERE ai_reason LIKE 'Analysis error%'"
).rowcount
db.commit()
print(f"Reset {n} failed posts — ready for re-analysis")
