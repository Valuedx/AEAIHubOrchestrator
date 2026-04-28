from app.database import SessionLocal, set_tenant_context
from app.models.workflow import WorkflowDefinition, WorkflowInstance

def find_data():
    tenants = ["default", "AEGEMS", "1", "system"]
    db = SessionLocal()
    try:
        for t in tenants:
            try:
                set_tenant_context(db, t)
                defs = db.query(WorkflowDefinition).all()
                if defs:
                    print(f"Found {len(defs)} definitions for tenant: {t}")
                    for d in defs:
                        print(f"  - {d.name} ({d.id})")
                
                insts = db.query(WorkflowInstance).all()
                if insts:
                    print(f"Found {len(insts)} instances for tenant: {t}")
            except Exception as e:
                print(f"Error for tenant {t}: {e}")
            db.rollback() # Clear transaction
    finally:
        db.close()

if __name__ == "__main__":
    find_data()
