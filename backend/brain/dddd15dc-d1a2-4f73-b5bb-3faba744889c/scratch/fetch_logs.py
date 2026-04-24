from sqlalchemy import create_engine, text
import json

engine = create_engine('postgresql://postgres:root@localhost:5432/ae_orchestrator_ai')
with engine.connect() as conn:
    rows = conn.execute(text("SELECT node_id, status, error, input_json, output_json FROM execution_logs WHERE instance_id = '5caa8abe-fe6a-4d08-b887-9d00e8092cd3' ORDER BY started_at ASC")).fetchall()
    for r in rows:
        print(f"Node: {r[0]}, Status: {r[1]}")
        if r[2]: print(f"Error: {r[2]}")
        if r[3]: print(f"Input: {json.dumps(r[3], indent=2)[:500]}")
        if r[4]: print(f"Output: {json.dumps(r[4], indent=2)[:2000]}")
        print('-'*40)
