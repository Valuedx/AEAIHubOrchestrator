
from sqlalchemy import create_engine, text

def find_hdfc():
    for db_name in ['ae_orchestrator_ai', 'ae_orchestrator']:
        db_url = f'postgresql://postgres:root@localhost:5432/{db_name}'
        print(f"Checking {db_name}...")
        engine = create_engine(db_url)
        with engine.connect() as conn:
            res = conn.execute(text("SELECT name, version FROM workflow_definitions WHERE name ILIKE '%HDFC%'")).fetchall()
            print(f"  {res}")

if __name__ == "__main__":
    find_hdfc()
