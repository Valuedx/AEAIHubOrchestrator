
from sqlalchemy import create_engine, text
import json
import os

def check_ops_routing():
    # Use environment variables if possible, but the scratch script had a hardcoded one that likely works in the user's env
    db_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai')
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id, name, graph_json FROM workflow_definitions WHERE name = 'AE_Ops_Routing' ORDER BY version DESC LIMIT 1")).fetchone()
        if res:
            wf_id, name, graph = res
            print(f"Workflow: {name} (ID: {wf_id})")
            
            # Look for nodes
            for node in graph.get('nodes', []):
                label = node.get('label') or node.get('data', {}).get('label')
                node_type = node.get('type') or node.get('data', {}).get('nodeType')
                print(f"Node ID: {node['id']}, Label: {label}, Type: {node_type}")
                
                config = node.get('data', {}).get('config', {})
                if label == 'ReAct Agent' or node_type == 'agent':
                    print(f"  Provider: {config.get('provider')}")
                    print(f"  Model: {config.get('model')}")
                    print(f"  Tools: {config.get('tools')}")
                    prompt = config.get('systemPrompt', '')
                    print(f"  System Prompt (first 500 chars): {prompt[:500]}...")
                
                if node_type == 'automationedge' or label == 'AutomationEdge':
                    print(f"  AE Workflow: {config.get('workflowName')}")
                    print(f"  AE Config: {config}")
        else:
            print("AE_Ops_Routing not found")

if __name__ == "__main__":
    check_ops_routing()
