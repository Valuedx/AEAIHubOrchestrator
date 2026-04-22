import sys
import os
from sqlalchemy import text
from sqlalchemy.orm import Session

# Add backend to path
sys.path.append(os.getcwd())

from app.database import SessionLocal, set_tenant_context

def check_mcp_servers():
    db: Session = SessionLocal()
    try:
        # Check all tenants
        tenants = db.execute(text("SELECT DISTINCT tenant_id FROM workflow_definitions")).fetchall()
        print(f"Active Tenants: {[t[0] for t in tenants]}")
        
        # Check per-tenant MCP servers
        rows = db.execute(text("SELECT id, tenant_id, label, url, is_default FROM tenant_mcp_servers")).fetchall()
        if not rows:
            print("No per-tenant MCP servers registered. Falling back to env defaults.")
        else:
            print("Registered Per-Tenant MCP Servers:")
            for row in rows:
                print(f" - {row.tenant_id}: {row.label} @ {row.url} (Default: {row.is_default})")

    except Exception as e:
        print(f"Error checking DB: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_mcp_servers()
