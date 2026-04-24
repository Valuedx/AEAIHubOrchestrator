import requests
import json

url = "http://localhost:5050/chat"
payload = {
    "session": "test_session_ops",
    "message": "check for running agents right now",
    "workflow_id": "693d1abb-084e-4024-8d96-5bf27a26d06f"
}
try:
    response = requests.post(url, json=payload, timeout=200)
    print("Status:", response.status_code)
    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)
except Exception as e:
    print(f"Error: {e}")
