
from sqlalchemy import create_engine, text
import json

def check_execution_logs():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    instance_id = 'b06c223b-33b9-448b-8033-bcb02d4aef5e'
    
    with engine.connect() as conn:
        res = conn.execute(text("SELECT node_id, status, error, output_json FROM execution_logs WHERE instance_id = :instance_id ORDER BY started_at ASC"), {"instance_id": instance_id}).fetchall()
        for row in res:
            print(f"Node: {row[0]}, Status: {row[1]}, Error: {row[2]}")
            # print(f"Output: {row[3]}")

if __name__ == "__main__":
    check_execution_logs()
