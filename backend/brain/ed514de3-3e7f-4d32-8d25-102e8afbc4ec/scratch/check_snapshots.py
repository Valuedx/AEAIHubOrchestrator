
from sqlalchemy import create_engine, text

def check_snapshots():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT workflow_def_id, version FROM workflow_snapshots WHERE workflow_def_id = (SELECT id FROM workflow_definitions WHERE name = 'AE_HDFC_WF' LIMIT 1)")).fetchall()
        print(f"Snapshots for AE_HDFC_WF: {res}")

if __name__ == "__main__":
    check_snapshots()
