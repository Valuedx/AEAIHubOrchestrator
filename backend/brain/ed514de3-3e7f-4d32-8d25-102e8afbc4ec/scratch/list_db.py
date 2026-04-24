
from app.config import settings
from sqlalchemy import create_engine, inspect

def list_tables():
    db_url = settings.database_url.replace("ae_orchestrator_ai", "ae_orchestrator")
    print(f"Connecting to: {db_url}")
    engine = create_engine(db_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"Tables: {tables}")
    
    if "workflow_definitions" in tables:
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            from sqlalchemy import text
            res = db.execute(text("SELECT count(*) FROM workflow_definitions")).scalar()
            print(f"Count in workflow_definitions: {res}")
            
            res = db.execute(text("SELECT name, version FROM workflow_definitions")).fetchall()
            print(f"Workflows in DB: {res}")
        finally:
            db.close()

if __name__ == "__main__":
    list_tables()
