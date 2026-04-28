
from sqlalchemy import create_engine, text

def find_everywhere():
    base_url = 'postgresql://postgres:root@localhost:5432/'
    engine_postgres = create_engine(base_url + "postgres")
    with engine_postgres.connect() as conn_p:
        dbs = conn_p.execute(text("SELECT datname FROM pg_database WHERE datistemplate = false;")).fetchall()
        for (db_name,) in dbs:
            try:
                engine = create_engine(base_url + db_name)
                with engine.connect() as conn:
                    # Check if workflow_definitions table exists
                    table_exists = conn.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'workflow_definitions');")).scalar()
                    if table_exists:
                        res = conn.execute(text("SELECT name, version FROM workflow_definitions WHERE name ILIKE '%HDFC%' OR name ILIKE '%Ops_Routing%'")).fetchall()
                        if res:
                            print(f"Found in {db_name}: {res}")
            except Exception:
                pass

if __name__ == "__main__":
    find_everywhere()
