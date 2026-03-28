import time
import requests
from mt5_connector import connect, get_account_info, get_open_positions

API_URL = "https://vestro-jpg.onrender.com/api"

print("🚀 Starting MT5 bridge...")

# Connect to MT5
connect()

while True:
    try:
        account = get_account_info()
        positions = get_open_positions()

        # Send to backend
        requests.post(f"{API_URL}/account/update", json=account)
        requests.post(f"{API_URL}/positions/update", json=positions)

        print("✅ Sent MT5 data:", account.get("balance"))

    except Exception as e:
        print("❌ Error:", e)

    time.sleep(5)