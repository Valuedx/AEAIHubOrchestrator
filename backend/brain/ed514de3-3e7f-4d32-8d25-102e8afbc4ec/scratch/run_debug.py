
import asyncio
import json
from app.engine.dag_runner import execute_graph
from app.database import SessionLocal, set_tenant_context
from app.models.workflow import WorkflowDefinition, WorkflowInstance

async def run_debug():
    tenant_id = "default"
    workflow_name = "AE_Ops_Routing"
    
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        wf_def = db.query(WorkflowDefinition).filter_by(name=workflow_name).order_by(WorkflowDefinition.version.desc()).first()
        if not wf_def:
            print("Workflow not found")
            return
        
        instance = WorkflowInstance(
            tenant_id=tenant_id,
            workflow_def_id=wf_def.id,
            trigger_payload={
                "message": "list of workflow",
                "session_id": "debug_session",
                "user_id": "debug_user"
            },
            status="queued",
            definition_version_at_start=wf_def.version,
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)
        
        print(f"Running workflow: {workflow_name} (ID: {wf_def.id}, Instance: {instance.id})")
        
        # We run it synchronously in a thread since execute_graph is sync
        def _run():
            session = SessionLocal()
            try:
                set_tenant_context(session, tenant_id)
                execute_graph(session, str(instance.id))
            finally:
                session.close()

        await asyncio.to_thread(_run)
        
        db.refresh(instance)
        print("Final Status:", instance.status)
        print("Result Context:", json.dumps(instance.context_json, indent=2))
        
    finally:
        db.close()

if __name__ == "__main__":
    import logging
    # Enable info logging to see the ReAct loop iterations
    logging.basicConfig(level=logging.INFO)
    # Also enable app.engine logging
    logging.getLogger("app.engine").setLevel(logging.INFO)
    
    asyncio.run(run_debug())
