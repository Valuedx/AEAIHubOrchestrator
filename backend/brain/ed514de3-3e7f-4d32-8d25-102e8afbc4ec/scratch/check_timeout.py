
from sqlalchemy import create_engine, text
import json

def check_timeout_instance():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    instance_id = '751476be-aaf4-417c-80fa-e25beadb36d2'
    
    with engine.connect() as conn:
        res = conn.execute(text("SELECT node_id, status, error, started_at, completed_at FROM execution_logs WHERE instance_id = :instance_id ORDER BY started_at ASC"), {"instance_id": instance_id}).fetchall()
        for row in res:
            print(f"Node: {row[0]}, Status: {row[1]}, Error: {row[2]}, Duration: {row[4]-row[3] if row[4] and row[3] else 'N/A'}")

if __name__ == "__main__":
    check_timeout_instance()
