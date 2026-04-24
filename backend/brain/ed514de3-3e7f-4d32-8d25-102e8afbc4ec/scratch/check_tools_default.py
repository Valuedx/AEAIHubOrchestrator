
from app.config import settings
from app.engine.mcp_client import list_tools

def check_tools():
    # List tools for the 'default' tenant
    tools = list_tools(tenant_id="default")
    print(f"Tools for default: {[t['name'] for t in tools]}")

if __name__ == "__main__":
    check_tools()
