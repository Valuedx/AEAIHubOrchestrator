import json
from sqlalchemy import create_engine, text

# Get DB URL from settings
import sys
sys.path.append('d:/AEAIHubOrchestrator/backend')
from app.config import settings

engine = create_engine(settings.database_url)

with engine.connect() as conn:
    print("Checking WorkflowDefinition.graph_json...")
    res = conn.execute(text("SELECT id, name FROM workflow_definitions WHERE graph_json::text ILIKE '%provider%google%'"))
    for row in res:
        print(f"Found in workflow: {row.name} ({row.id})")
    
    print("\nChecking WorkflowDraft.graph_json...")
    res = conn.execute(text("SELECT id FROM workflow_drafts WHERE graph_json::text ILIKE '%provider%google%'"))
    for row in res:
        print(f"Found in draft: {row.id}")

    print("\nChecking CopilotSession.provider...")
    res = conn.execute(text("SELECT id FROM copilot_sessions WHERE provider = 'google'"))
    for row in res:
        print(f"Found in session: {row.id}")
