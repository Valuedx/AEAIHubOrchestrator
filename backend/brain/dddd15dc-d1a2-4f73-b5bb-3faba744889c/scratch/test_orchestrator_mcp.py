import sys
import logging
logging.basicConfig(level=logging.DEBUG)

sys.path.append(r"d:\AEAIHubOrchestrator\backend")

from app.engine.mcp_client import list_tools, invalidate_tool_cache

try:
    print("Invalidating tool cache...")
    invalidate_tool_cache()
    print("Fetching tools for tenant 'default'...")
    tools = list_tools(tenant_id="default", server_label="mcp_server")
    print(f"Success! Retrieved {len(tools)} tools.")
    for t in tools[:5]:
        print(f" - {t['name']}")
except Exception as e:
    import traceback
    traceback.print_exc()
