
from sqlalchemy import create_engine, text
import json

def check_ops_routing():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT graph_json FROM workflow_definitions WHERE name = 'AE_Ops_Routing' ORDER BY version DESC LIMIT 1")).fetchone()
        if res:
            graph = res[0]
            # print(json.dumps(graph, indent=2))
            
            # Look for nodes
            for node in graph.get('nodes', []):
                label = node.get('label') or node.get('data', {}).get('label')
                print(f"Node ID: {node['id']}, Label: {label}")
                if label == 'ReAct Agent':
                    config = node.get('data', {}).get('config', {})
                    print(f"  Provider: {config.get('provider')}")
                    print(f"  Model: {config.get('model')}")
                    print(f"  Tools: {config.get('tools')}")
                    # print(f"System Prompt: {config.get('systemPrompt')[:200]}...")
        else:
            print("AE_Ops_Routing not found")

if __name__ == "__main__":
    check_ops_routing()
