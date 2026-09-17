from dotenv import load_dotenv
load_dotenv()
from campaign_brain import sync_campaigns, init_tables
import os

init_tables()
accounts = [
    ("Account 1", os.getenv("META_AD_ACCOUNT_1", "")),
    ("Account 2", os.getenv("META_AD_ACCOUNT_2", "")),
    ("Account 3", os.getenv("META_AD_ACCOUNT_3", "")),
]
n = sync_campaigns(accounts)
print(f"Re-synced {n} campaigns with corrected lead/message counts")
