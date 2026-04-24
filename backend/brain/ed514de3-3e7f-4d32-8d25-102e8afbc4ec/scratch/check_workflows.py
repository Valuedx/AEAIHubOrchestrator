
from app.database import SessionLocal
from app.models.workflow import WorkflowDefinition, WorkflowInstance
from sqlalchemy import desc

from app.config import settings

def check_workflows():
    db_url = settings.database_url
    print(f"Connecting to: {db_url}")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # Check AE_HDFC_workflow
        hdfc = db.query(WorkflowDefinition).filter(WorkflowDefinition.name.like("%AE_HDFC_workflow%")).order_by(desc(WorkflowDefinition.version)).first()
        if hdfc:
            print(f"Found HDFC Workflow: {hdfc.name} (v{hdfc.version}) ID: {hdfc.id}")
            # print(f"Nodes: {hdfc.definition_json.get('nodes', [])}")
        else:
            print("HDFC Workflow not found")

        # Check AE_Ops_Routing
        ops = db.query(WorkflowDefinition).filter(WorkflowDefinition.name.like("%AE_Ops_Routing%")).order_by(desc(WorkflowDefinition.version)).first()
        if ops:
            print(f"Found Ops Workflow: {ops.name} (v{ops.version}) ID: {ops.id}")
            # print(f"Nodes: {ops.definition_json.get('nodes', [])}")
        else:
            print("Ops Workflow not found")
            
        # List all workflows
        all_wfs = db.query(WorkflowDefinition.name, WorkflowDefinition.version).all()
        print(f"All workflows: {all_wfs}")

    finally:
        db.close()

if __name__ == "__main__":
    check_workflows()
