import sqlalchemy
from sqlalchemy.orm import sessionmaker
import sys

DATABASE_URL = "postgresql://postgres:root@localhost:5432/ae_orchestrator"
engine = sqlalchemy.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    instance_id = "30d20c6e-1a9b-4694-a1e2-e761f3713b1f"
    res = session.execute(
        sqlalchemy.text("SELECT node_id, status, error FROM execution_logs WHERE instance_id = :id AND status = 'failed'"),
        {"id": instance_id}
    ).fetchall()
    
    if res:
        for row in res:
            print(f"Node: {row[0]}, Status: {row[1]}, Error: {row[2]}")
    else:
        print("No failed nodes found for this instance.")
finally:
    session.close()
