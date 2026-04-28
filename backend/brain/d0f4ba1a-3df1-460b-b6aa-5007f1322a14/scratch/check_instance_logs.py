from app.database import SessionLocal, set_tenant_context
from app.models.workflow import WorkflowInstance, ExecutionLog
import json

def check_instance():
    db = SessionLocal()
    try:
        set_tenant_context(db, "default")
        instance = db.query(WorkflowInstance).filter_by(id="bab7c006-8e0f-4002-9703-1bba0b0991b9").first()
        if not instance:
            print("Instance not found in 'default' tenant")
            return
        
        print(f"Instance ID: {instance.id}")
        print(f"Status: {instance.status}")
        print(f"Workflow Def ID: {instance.workflow_def_id}")
        print(f"Created At: {instance.created_at}")
        
        logs = db.query(ExecutionLog).filter_by(instance_id=instance.id).order_by(ExecutionLog.started_at).all()
        print(f"Execution Logs ({len(logs)}):")
        for log in logs:
            print(f"  Node: {log.node_id} ({log.node_type}), Status: {log.status}")
            if log.error:
                print(f"    Error: {log.error}")
            if log.output_json:
                # Truncate output for display
                out_str = json.dumps(log.output_json)
                if len(out_str) > 200:
                    out_str = out_str[:200] + "..."
                print(f"    Output: {out_str}")

    finally:
        db.close()

if __name__ == "__main__":
    check_instance()
