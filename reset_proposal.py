import sqlite3
db = sqlite3.connect("ads_manager.db")
db.execute("UPDATE ad_proposals SET status='APPROVED', execution_result=NULL, executed_at=NULL WHERE id=55")
db.commit()
print("Reset done")
db.close()
