import sqlalchemy
from sqlalchemy import text, create_engine
import os

def verify_as_app():
    # Connect as the restricted app user
    db_url = "postgresql://ae_orchestrator_app:root@localhost:5432/ae_orchestrator_ai"
    engine = create_engine(db_url)
    
    try:
        with engine.connect() as conn:
            # Try to select from the newly created table
            res = conn.execute(text("SELECT * FROM tenant_policies LIMIT 1"))
            print("Successfully selected from tenant_policies as ae_orchestrator_app")
            
            # Check table structure for migration 0021 columns
            res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenant_policies'"))
            columns = [row[0] for row in res]
            print(f"Columns in tenant_policies: {columns}")
            
            # Check for 0021 columns: rate_limit_requests_per_window
            if 'rate_limit_requests_per_window' in columns:
                print("Migration 0021 columns present.")
            else:
                print("Migration 0021 columns MISSING!")
                
    except Exception as e:
        print(f"Error as app user: {e}")

def verify_as_superuser():
    # Connect as postgres to check RLS and ownership
    db_url = "postgresql://postgres:root@localhost:5432/ae_orchestrator_ai"
    engine = create_engine(db_url)
    
    try:
        with engine.connect() as conn:
            # Check RLS status
            res = conn.execute(text("SELECT tablename, rowsecurity FROM pg_tables WHERE tablename = 'tenant_policies'"))
            row = res.fetchone()
            print(f"Table 'tenant_policies' RLS enabled: {row[1]}")
            
    except Exception as e:
        print(f"Error as superuser: {e}")

if __name__ == "__main__":
    verify_as_app()
    print("-" * 20)
    verify_as_superuser()
