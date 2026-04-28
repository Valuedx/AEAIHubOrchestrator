import json
from app.database import SessionLocal
from app.models.workflow import WorkflowDefinition

def check_workflow():
    db = SessionLocal()
    try:
        wf = db.query(WorkflowDefinition).filter_by(id="693d1abb-084e-4024-8d96-5bf27a26d06f").first()
        if not wf:
            print("Workflow not found")
            return
        print(f"Workflow Name: {wf.name}")
        print(f"Graph JSON: {json.dumps(wf.graph_json, indent=2)}")
    finally:
        db.close()

if __name__ == "__main__":
    check_workflow()
