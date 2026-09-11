"""Clear all load-gen messages from all 3 backends before a test run."""
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BACKENDS = [
    "https://10.1.75.53:5238",  # Sys2 mapped port
    "https://10.1.75.53:5239",  # Sys3 mapped port
    "https://10.1.75.53:5240",  # Sys4 mapped port
]

for url in BACKENDS:
    try:
        r = requests.post(f"{url}/clear", verify=False, timeout=5)
        print(f"{url}/clear -> {r.status_code}: {r.text.strip()}")
    except Exception as e:
        print(f"{url}/clear -> ERROR: {e}")

print("\nAll backends cleared!")
