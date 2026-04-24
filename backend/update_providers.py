import sys
import json
import logging
sys.path.append('d:/AEAIHubOrchestrator/backend')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from app.models.workflow import WorkflowDefinition
from app.models.copilot import CopilotSession, WorkflowDraft, CopilotAcceptedPattern

# Use the postgres superuser to bypass RLS
engine = create_engine('postgresql://postgres:root@localhost:5432/ae_orchestrator_ai')
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

def fix_graph_json(graph):
    if not isinstance(graph, dict):
        return False
    changed = False
    for node in graph.get('nodes', []):
        if 'config' in node and isinstance(node['config'], dict):
            provider = node['config'].get('provider')
            if provider == 'google':
                node['config']['provider'] = 'vertex'
                changed = True
    return changed

def run():
    count_wf = 0
    # 1. Update WorkflowDefinition
    wf_defs = db.query(WorkflowDefinition).all()
    for wf in wf_defs:
        if fix_graph_json(wf.graph_json):
            flag_modified(wf, "graph_json")
            count_wf += 1
            
    count_draft = 0
    # 2. Update WorkflowDraft
    drafts = db.query(WorkflowDraft).all()
    for draft in drafts:
        if fix_graph_json(draft.graph_json):
            flag_modified(draft, "graph_json")
            count_draft += 1

    count_session = 0
    # 3. Update CopilotSession
    sessions = db.query(CopilotSession).filter(CopilotSession.provider == 'google').all()
    for s in sessions:
        s.provider = 'vertex'
        count_session += 1

    count_pattern = 0
    # 4. Update CopilotAcceptedPattern
    patterns = db.query(CopilotAcceptedPattern).all()
    for p in patterns:
        if fix_graph_json(p.graph_json):
            flag_modified(p, "graph_json")
            count_pattern += 1

    db.commit()
    print(f"Database updated successfully. Updated {count_wf} workflows, {count_draft} drafts, {count_session} sessions, and {count_pattern} patterns.")

if __name__ == '__main__':
    run()
