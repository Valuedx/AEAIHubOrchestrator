from app.database import SessionLocal
from app.models.workflow import WorkflowInstance

def check_instance():
    db = SessionLocal()
    try:
        instance = db.query(WorkflowInstance).filter_by(id="bab7c006-8e0f-4002-9703-1bba0b0991b9").first()
        if not instance:
            print("Instance not found")
            return
        print(f"Instance ID: {instance.id}")
        print(f"Status: {instance.status}")
        print(f"Workflow Def ID: {instance.workflow_def_id}")
        print(f"Tenant ID: {instance.tenant_id}")
        if instance.context_json:
            import json
            print(f"Context: {json.dumps(instance.context_json, indent=2)}")
    finally:
        db.close()

if __name__ == "__main__":
    check_instance()
