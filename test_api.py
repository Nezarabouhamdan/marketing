import requests

try:
    r = requests.get('http://127.0.0.1:5000/api/ads')
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Number of accounts: {len(data)}")
        if data:
            acct = data[0]
            print(f"Account: {acct.get('name', 'Unknown')}")
            print(f"Ads count: {len(acct.get('ads', []))}")
            if acct.get('ads'):
                ad = acct['ads'][0]
                print(f"First ad has keys: {list(ad.keys())}")
                print("✅ API working with expanded fields")
            else:
                print("No ads in account")
    else:
        print(f"Error: {r.text}")
except Exception as e:
    print(f"Error: {e}")