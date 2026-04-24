from app.database import SessionLocal
from app.models.workflow import WorkflowDefinition

def list_workflows():
    db = SessionLocal()
    try:
        wfs = db.query(WorkflowDefinition).all()
        for wf in wfs:
            print(f"ID: {wf.id}, Name: {wf.name}")
    finally:
        db.close()

if __name__ == "__main__":
    list_workflows()
