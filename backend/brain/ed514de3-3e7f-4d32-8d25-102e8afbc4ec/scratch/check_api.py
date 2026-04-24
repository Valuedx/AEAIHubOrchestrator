
import httpx

def check_api():
    try:
        resp = httpx.get("http://localhost:8001/api/v1/workflows", headers={"X-Tenant-Id": "tester_user"})
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            wfs = resp.json()
            print(f"Workflows: {[wf.get('name') for wf in wfs]}")
            for wf in wfs:
                if "AE_Ops_Routing" in wf.get("name", ""):
                    print(f"Found it: {wf}")
    except Exception as exc:
        print(f"API call failed: {exc}")

if __name__ == "__main__":
    check_api()
