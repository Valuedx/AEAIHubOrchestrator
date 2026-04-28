
from sqlalchemy import create_engine, text
import json

def check_routing_variants():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT name, version, graph_json FROM workflow_definitions WHERE name IN ('Ops Routing', 'OPS_Routing_v2')")).fetchall()
        for name, version, graph in res:
            print(f"Workflow: {name} (v{version})")
            for node in graph.get('nodes', []):
                label = node.get('label') or node.get('data', {}).get('label')
                if label == 'ReAct Agent':
                    config = node.get('data', {}).get('config', {})
                    print(f"  Node ID: {node['id']}, Label: {label}")
                    print(f"    Provider: {config.get('provider')}")
                    print(f"    Tools: {config.get('tools')}")

if __name__ == "__main__":
    check_routing_variants()
