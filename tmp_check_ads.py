import requests
r = requests.get('http://127.0.0.1:5000/api/ads')
print('status', r.status_code)
print(r.text[:2000])
