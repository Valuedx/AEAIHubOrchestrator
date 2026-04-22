import sys
import os
import asyncio
import json

# Add backend to path
sys.path.append(os.getcwd())

from app.engine.mcp_client import list_tools, invalidate_tool_cache

def test_mcp():
    print("--- Testing MCP Connectivity ---")
    
    # 1. Clear cache to ensure a fresh fetch
    invalidate_tool_cache()
    
    # 2. Test Env Fallback (based on ORCHESTRATOR_MCP_SERVER_URL in .env)
    print("\n[Test 1] Global Fallback (__env__)")
    try:
        tools = list_tools()
        if tools:
            print(f"Success! Found {len(tools)} tools:")
            for t in tools:
                print(f" - {t['name']}: {t.get('description', 'No description')[:50]}...")
        else:
            print("No tools found (empty response or server unreachable).")
    except Exception as e:
        print(f"FAILED: {e}")

    # 3. Test HDFC Tenant (based on registered row)
    print("\n[Test 2] Tenant 'hdfc'")
    try:
        tools = list_tools(tenant_id="hdfc")
        if tools:
            print(f"Success! Found {len(tools)} tools:")
            for t in tools:
                print(f" - {t['name']}: {t.get('description', 'No description')[:50]}...")
        else:
            print("No tools found for tenant 'hdfc'.")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    test_mcp()
