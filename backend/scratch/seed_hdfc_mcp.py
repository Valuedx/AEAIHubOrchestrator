import sys
import os
import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session

# Add backend to path
sys.path.append(os.getcwd())

from app.database import SessionLocal, set_tenant_context, tenant_id_context

def seed_hdfc_mcp():
    tenant_id = "hdfc"
    mcp_url = "http://127.0.0.1:3000/mcp"
    print(f"Configuring MCP server for tenant '{tenant_id}' -> {mcp_url}")
    
    db: Session = SessionLocal()
    try:
        # 1. Set the RLS context for the "hdfc" tenant
        set_tenant_context(db, tenant_id)
        
        # 2. Clear existing defaults for this tenant
        db.execute(
            text("UPDATE tenant_mcp_servers SET is_default = FALSE WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        )
        
        # 3. Upsert the server
        # We check within the RLS-constrained session
        existing = db.execute(
            text("SELECT id FROM tenant_mcp_servers WHERE label = :label"),
            {"label": "hdfc-local"}
        ).fetchone()
        
        if existing:
            db.execute(
                text("UPDATE tenant_mcp_servers SET url = :url, is_default = TRUE WHERE id = :id"),
                {"url": mcp_url, "id": existing[0]}
            )
            print(f"Updated existing MCP server record (ID: {existing[0]})")
        else:
            new_id = uuid.uuid4()
            db.execute(
                text("""
                    INSERT INTO tenant_mcp_servers (id, tenant_id, label, url, auth_mode, config_json, is_default, created_at, updated_at)
                    VALUES (:id, :tid, :label, :url, 'none', '{}', TRUE, now(), now())
                """),
                {"id": new_id, "tid": tenant_id, "label": "hdfc-local", "url": mcp_url}
            )
            print(f"Created new MCP server record (ID: {new_id})")
        
        db.commit()
        print("Success: MCP server configured for HDFC workflow.")

    except Exception as e:
        print(f"Error seeding MCP server: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_hdfc_mcp()
