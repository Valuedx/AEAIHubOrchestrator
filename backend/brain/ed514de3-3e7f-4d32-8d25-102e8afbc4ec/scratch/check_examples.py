
from app.engine.mcp_client import list_tools

def check_ae_tools():
    tools = list_tools(tenant_id="default")
    for t in tools:
        if 'examples' in t['parameters']:
            print(f"Tool {t['name']} has examples in parameters")
        for prop in t['parameters'].get('properties', {}).values():
            if isinstance(prop, dict) and 'examples' in prop:
                print(f"Tool {t['name']} has examples in property")

if __name__ == "__main__":
    check_ae_tools()
