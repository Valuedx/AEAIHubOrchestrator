
import httpx
import json

# Hardcoded from .env seen in AE_Canvas
AE_BASE_URL = "https://t4.automationedge.com"
AE_USERNAME = "Pooja A"
AE_PASSWORD = "Edge@2026"
AE_ORG_CODE = "AEGEMS"

def test_t4_connectivity():
    rest_base = "/aeengine/rest"
    auth_url = f"{AE_BASE_URL}{rest_base}/authenticate"
    
    print(f"Testing auth at {auth_url}...")
    try:
        with httpx.Client(verify=False) as client:
            resp = client.post(
                auth_url,
                data={"username": AE_USERNAME, "password": AE_PASSWORD},
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0
            )
            print(f"Status Code: {resp.status_code}")
            if resp.status_code != 200:
                print(f"Response: {resp.text}")
                return
            
            data = resp.json()
            token = data.get("sessionToken") or data.get("token")
            if not token:
                print("No token in response")
                print(f"Data: {data}")
                return
            
            print("Auth successful!")
            
            # Now try to list workflows
            # catalogue_url = f"{AE_BASE_URL}{rest_base}/{AE_ORG_CODE}/workflows/catalogue"
            # Try a few paths
            paths = [
                f"{rest_base}/{AE_ORG_CODE}/workflows/catalogue",
                f"{rest_base}/workflows/catalogue",
                f"{rest_base}/{AE_ORG_CODE}/workflows",
                f"{rest_base}/workflows"
            ]
            
            for path in paths:
                url = f"{AE_BASE_URL}{path}"
                print(f"Trying to list workflows at {url}...")
                resp = client.get(
                    url,
                    headers={"Accept": "application/json", "X-session-token": token},
                    timeout=10.0
                )
                print(f"  Status Code: {resp.status_code}")
                if resp.status_code == 200:
                    wf_data = resp.json()
                    print(f"  Response type: {type(wf_data)}")
                    if isinstance(wf_data, dict):
                        print(f"  Keys: {list(wf_data.keys())}")
                    # Count workflows
                    wfs = []
                    if isinstance(wf_data, list):
                        wfs = wf_data
                    elif isinstance(wf_data, dict):
                        for k in ("workflows", "items", "results", "data"):
                            if isinstance(wf_data.get(k), list):
                                wfs = wf_data[k]
                                break
                    print(f"  Found {len(wfs)} workflows at this endpoint.")
                    if len(wfs) > 0:
                        for i, wf in enumerate(wfs[:5]):
                            print(f"    - {wf.get('name') or wf.get('workflowName')}")
                else:
                    print(f"  Failed: {resp.text[:200]}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_t4_connectivity()
