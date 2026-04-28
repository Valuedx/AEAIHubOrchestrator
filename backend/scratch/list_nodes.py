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
            node_id = node.get("id")
            node_type = node.get("type")
            # In React Flow, data is often a separate key
            node_data = node.get("data", {})
            label = node_data.get("label")
            config = node_data.get("config", {})
            print(f"Node ID: {node_id}, Type: {node_type}, Label: {label}, Config: {json.dumps(config)}")
    else:
        print("Workflow definition not found")
finally:
    session.close()
