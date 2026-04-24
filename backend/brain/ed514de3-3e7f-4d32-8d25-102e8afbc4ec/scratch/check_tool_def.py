
from app.engine.mcp_client import list_tools

def check_ae_tool():
    tools = list_tools(tenant_id="default")
    for t in tools:
        if t['name'] == 'ae.workflow.list':
            import json
            print(json.dumps(t, indent=2))

if __name__ == "__main__":
    check_ae_tool()
