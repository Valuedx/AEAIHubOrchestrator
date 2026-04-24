
from app.config import settings
from sqlalchemy import create_engine, text

def check_mcp_servers():
    db_url = settings.database_url
    print(f"Connecting to: {db_url}")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT * FROM tenant_mcp_servers")).fetchall()
        print(f"MCP Servers: {res}")

if __name__ == "__main__":
    check_mcp_servers()
