
from sqlalchemy import create_engine, text

def check_ai_db():
    # Use postgres superuser for ae_orchestrator_ai
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    print(f"Connecting to: {db_url}")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        try:
            res = conn.execute(text("SELECT name, version FROM workflow_definitions")).fetchall()
            print(f"Workflows in ae_orchestrator_ai: {res}")
        except Exception as exc:
            print(f"Failed to query ae_orchestrator_ai: {exc}")

if __name__ == "__main__":
    check_ai_db()
