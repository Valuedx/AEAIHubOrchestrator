import sqlalchemy
from sqlalchemy.orm import sessionmaker
import json
import sys

DATABASE_URL = "postgresql://postgres:root@localhost:5432/ae_orchestrator"
engine = sqlalchemy.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    workflow_def_id = "0d9f9e4a-e700-4360-b374-6ea570c90019"
    res = session.execute(sqlalchemy.text("SELECT graph_json FROM workflow_definitions WHERE id = :id"), {"id": workflow_def_id}).fetchone()
    if res:
        graph = res[0]
        if isinstance(graph, str):
            graph = json.loads(graph)
        
        nodes = graph.get("nodes", [])
        for node in nodes:
            node_data = node.get("data", {})
            config = node_data.get("config", {})
            if config.get("provider") == "google":
                print(f"Node ID: {node.get('id')}, Label: {node_data.get('label')}, Config: {json.dumps(config)}")
    else:
        print("Workflow definition not found")
finally:
    session.close()
