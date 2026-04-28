import sqlalchemy
from sqlalchemy import text, create_engine
import os

# Try to connect as postgres superuser
# Assuming password 'root' as per the app user's password in .env
# Database name 'ae_orchestrator_ai' from .env
db_url = "postgresql://postgres:root@localhost:5432/ae_orchestrator_ai"

engine = create_engine(db_url)

sql_commands = [
    # Provision ae_orchestrator_app
    """DO $$ 
    BEGIN 
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ae_orchestrator_app') THEN
            CREATE ROLE ae_orchestrator_app WITH LOGIN PASSWORD 'root';
        END IF;
    END $$;""",
    "GRANT CONNECT ON DATABASE ae_orchestrator_ai TO ae_orchestrator_app;",
    "GRANT USAGE ON SCHEMA public TO ae_orchestrator_app;",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ae_orchestrator_app;",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ae_orchestrator_app;",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ae_orchestrator_app;",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ae_orchestrator_app;",

    # Provision ae_orchestrator_beat
    """DO $$ 
    BEGIN 
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ae_orchestrator_beat') THEN
            CREATE ROLE ae_orchestrator_beat WITH LOGIN PASSWORD 'root' BYPASSRLS;
        END IF;
    END $$;""",
    "GRANT CONNECT ON DATABASE ae_orchestrator_ai TO ae_orchestrator_beat;",
    "GRANT USAGE ON SCHEMA public TO ae_orchestrator_beat;",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ae_orchestrator_beat;",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ae_orchestrator_beat;",
]

def run_setup():
    with engine.connect() as conn:
        for cmd in sql_commands:
            try:
                print(f"Executing: {cmd.strip().splitlines()[0]}...")
                conn.execute(text(cmd))
                conn.commit()
            except Exception as e:
                print(f"Error executing command: {e}")
                conn.rollback() # Rollback and continue to next command

if __name__ == "__main__":
    run_setup()
