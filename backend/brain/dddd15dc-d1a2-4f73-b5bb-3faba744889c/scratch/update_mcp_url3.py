from sqlalchemy import create_engine, text
engine = create_engine('postgresql://postgres:root@localhost:5432/ae_orchestrator_ai')
with engine.begin() as conn:
    conn.execute(text("UPDATE tenant_mcp_servers SET url = 'http://127.0.0.1:3000/mcp' WHERE tenant_id = 'default' AND label = 'mcp_server'"))
print("Done")
