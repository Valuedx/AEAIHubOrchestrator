import json
from sqlalchemy import create_engine, text

e = create_engine("postgresql://postgres:root@localhost:5432/ae_orchestrator")
conn = e.connect()
r = conn.execute(text("SELECT graph_json FROM workflow_definitions WHERE name='HDFC_WF'"))
g = r.fetchone()[0]
nodes = {n["id"]: n for n in g["nodes"]}

for nid in ["node_7", "node_8", "node_10"]:
    n = nodes[nid]
    cfg = n["data"]["config"]
    print(f"=== {nid}: {n['data'].get('displayName','')} ===")
    print(f"  tools: {json.dumps(cfg.get('tools', []))}")
    print(f"  maxIterations: {cfg.get('maxIterations')}")
    print(f"  systemPrompt (first 120 chars): {cfg.get('systemPrompt','')[:120]}")
    print()

conn.close()
