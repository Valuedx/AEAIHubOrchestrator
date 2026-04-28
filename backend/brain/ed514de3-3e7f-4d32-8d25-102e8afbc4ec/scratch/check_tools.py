
from app.config import settings
from app.engine.mcp_client import list_tools

def check_tools():
    # List tools for the default tenant
    tools = list_tools(tenant_id="tester_user")
    print(f"Tools for tester_user: {[t['name'] for t in tools]}")
    
    # List tools for None (env fallback)
    tools = list_tools(tenant_id=None)
    print(f"Tools for None: {[t['name'] for t in tools]}")

if __name__ == "__main__":
    check_tools()
