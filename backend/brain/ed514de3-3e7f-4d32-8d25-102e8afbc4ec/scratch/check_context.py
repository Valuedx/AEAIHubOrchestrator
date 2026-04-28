
from sqlalchemy import create_engine, text
import json

def check_context():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    instance_id = '751476be-aaf4-417c-80fa-e25beadb36d2'
    
    with engine.connect() as conn:
        res = conn.execute(text("SELECT context_json FROM workflow_instances WHERE id = :instance_id"), {"instance_id": instance_id}).fetchone()
        if res:
            ctx = res[0]
            # print(json.dumps(ctx, indent=2))
            # Look for node_10's intermediate state if it exists
            node_10_ctx = ctx.get('node_10', {})
            print(f"Node 10 Status: {node_10_ctx.get('status')}")
            # The react_loop stores iterations in the node context during execution? 
            # Actually, it might only store it at the end.
        else:
            print("Instance not found")

if __name__ == "__main__":
    check_context()
