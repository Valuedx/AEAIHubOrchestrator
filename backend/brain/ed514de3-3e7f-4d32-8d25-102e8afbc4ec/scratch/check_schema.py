
from sqlalchemy import create_engine, inspect

def check_schema():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    inspector = inspect(engine)
    columns = inspector.get_columns('execution_logs')
    for col in columns:
        print(f"Column: {col['name']}")

if __name__ == "__main__":
    check_schema()
