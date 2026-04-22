import sys
import os
import json
from sqlalchemy import text
from sqlalchemy.orm import Session

# Add backend to path
sys.path.append(os.getcwd())

from app.database import SessionLocal, set_tenant_context

def fetch_logs():
    wf_id = 'f4a7f02f-78a7-4252-8e7c-d6db869b5eee'
    print(f"Fetching logs for Workflow ID: {wf_id}")
    
    db: Session = SessionLocal()
    try:
        # 1. Set tenant context (assuming default for the A2A bridge)
        db.execute(text("SET app.tenant_id = 'default'"))
        
        # 2. Get latest instance
        inst = db.execute(
            text("SELECT id, status, created_at FROM workflow_instances WHERE workflow_def_id = :wf_id ORDER BY created_at DESC LIMIT 1"),
            {'wf_id': wf_id}
        ).fetchone()
        
        if not inst:
            print("No instances found for this workflow.")
            return

        instance_id = inst[0]
        status = inst[1]
        created_at = inst[2]
        print(f"Found Instance: {instance_id}")
        print(f"Status: {status}")
        print(f"Created At: {created_at}")
        print("-" * 50)
        
        # 3. Fetch Node Logs
        logs = db.execute(
            text("""
                SELECT node_id, node_type, status, output_json, error, started_at, completed_at 
                FROM execution_logs 
                WHERE instance_id = :iid 
                ORDER BY started_at ASC
            """),
            {'iid': instance_id}
        ).fetchall()
        
        if not logs:
            print("No execution logs found for this instance yet.")
            return

        for log in logs:
            node_id, node_type, node_status, output, error, start, end = log
            print(f"Node: {node_id} ({node_type})")
            print(f"  Status: {node_status}")
            if error:
                print(f"  Error: {error}")
            if output:
                # Truncate output for readability
                out_str = json.dumps(output, indent=2)
                if len(out_str) > 200:
                    out_str = out_str[:200] + "... [truncated]"
                print(f"  Output: {out_str}")
            print("-" * 30)

    except Exception as e:
        print(f"Error fetching logs: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    fetch_logs()
