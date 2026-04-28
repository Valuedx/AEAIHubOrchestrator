from app.database import engine
from sqlalchemy import text

def check_db():
    with engine.connect() as conn:
        # Check tables
        tables = conn.execute(text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public';")).fetchall()
        print("Tables:", [t[0] for t in tables])
        
        # Check row counts
        for table in ["workflow_definitions", "workflow_instances", "execution_logs"]:
            count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"Table {table} count: {count}")

if __name__ == "__main__":
    check_db()
