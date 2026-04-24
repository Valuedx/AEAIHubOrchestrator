
from sqlalchemy import create_engine, text

def check_hdfc_workflow():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT name, version FROM workflow_definitions WHERE name = 'AE_HDFC_workflow'")).fetchall()
        print(f"AE_HDFC_workflow: {res}")

if __name__ == "__main__":
    check_hdfc_workflow()
