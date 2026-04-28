import sqlalchemy
from sqlalchemy.orm import sessionmaker
import json
import sys
import uuid

DATABASE_URL = "postgresql://postgres:root@localhost:5432/ae_orchestrator"
engine = sqlalchemy.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    # Find the instance first
    instance_id = "6113be2b-2f9e-4979-ad28-e653ad4b44a9"
    result = session.execute(sqlalchemy.text("SELECT workflow_def_id FROM workflow_instances WHERE id = :id"), {"id": instance_id}).fetchone()
    if not result:
        print("Instance not found")
        sys.exit(1)
    
    workflow_def_id = result[0]
    print(f"Workflow Definition ID: {workflow_def_id}")

    # Get the definition
    res = session.execute(sqlalchemy.text("SELECT graph_json FROM workflow_definitions WHERE id = :id"), {"id": workflow_def_id}).fetchone()
    if res:
        graph = res[0]
        # graph might be a string or a dict
        if isinstance(graph, str):
            graph = json.loads(graph)
        
        nodes = graph.get("nodes", [])
        for node in nodes:
            if node.get("id") == "node_3":
                print(f"Node 3 Config: {json.dumps(node.get('config', {}), indent=2)}")
    else:
        print("Workflow definition not found")
finally:
    session.close()
