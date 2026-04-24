import os
import sys

sys.path.append(r"d:\AE_Canvas")
from mcp_server.ae_client import get_ae_client
import httpx

client = get_ae_client()
print("Base URL:", client.base_url)
print("Auth:", client.authenticate())

try:
    print("Listing workflows via AEClient...")
    wfs = client.search_workflows()
    print(f"Found {len(wfs)} workflows!")
    for wf in wfs[:5]:
        print(f" - {wf.get('workflowName') or wf.get('name')}")
except Exception as e:
    import traceback
    traceback.print_exc()

client.close()
