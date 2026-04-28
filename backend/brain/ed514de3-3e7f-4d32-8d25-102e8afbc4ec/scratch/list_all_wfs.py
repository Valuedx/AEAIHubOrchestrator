
from sqlalchemy import create_engine, text

def list_all_wfs():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id, name, version, tenant_id FROM workflow_definitions")).fetchall()
        for row in res:
            print(row)

if __name__ == "__main__":
    list_all_wfs()
