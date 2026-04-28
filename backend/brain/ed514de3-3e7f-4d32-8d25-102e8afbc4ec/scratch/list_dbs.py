
from app.config import settings
from sqlalchemy import create_engine, text

def list_databases():
    # Connect to 'postgres' database to list all databases
    base_url = settings.database_url.rsplit("/", 1)[0] + "/postgres"
    print(f"Connecting to: {base_url}")
    engine = create_engine(base_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT datname FROM pg_database WHERE datistemplate = false;")).fetchall()
        print(f"Databases: {res}")

if __name__ == "__main__":
    list_databases()
