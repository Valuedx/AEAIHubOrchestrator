
from sqlalchemy import create_engine, text

def check_drafts():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id, name, tenant_id FROM workflow_drafts")).fetchall()
        for row in res:
            print(row)

if __name__ == "__main__":
    check_drafts()
