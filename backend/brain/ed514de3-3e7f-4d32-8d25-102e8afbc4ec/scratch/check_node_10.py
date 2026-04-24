
from sqlalchemy import create_engine, text
import json

def check_node_10():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT graph_json FROM workflow_definitions WHERE name = 'AE_Ops_Routing' ORDER BY version DESC LIMIT 1")).fetchone()
        if res:
            graph = res[0]
            for node in graph.get('nodes', []):
                if node['id'] == 'node_10':
                    print(json.dumps(node, indent=2))
        else:
            print("AE_Ops_Routing not found")

if __name__ == "__main__":
    check_node_10()
