import json
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DB_URL = "postgresql://postgres:root@localhost:5432/ae_orchestrator"
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
session = Session()

DIAGNOSTIC_TOOLS = [
    "ae.request.get_summary", 
    "ae.request.get_failure_message", 
    "ae.request.get_logs", 
    "ae.agent.get_status", 
    "ae.agent.analyze_logs", 
    "ae.support.diagnose_failed_request", 
    "ae.support.get_system_health"
]

REMEDIATION_TOOLS = [
    "ae.agent.restart_service", 
    "ae.agent.get_status", 
    "ae.request.get_summary"
]

DEFAULT_OPS_TOOLS = [
    "ae.request.search",
    "ae.agent.get_status",
    "ae.support.get_system_health"
]

try:
    # 1. Fetch the workflow
    query = text("SELECT id, graph_json FROM workflow_definitions WHERE name = 'HDFC_WF'")
    row = session.execute(query).fetchone()
    
    if not row:
        print("HDFC_WF not found")
        exit(1)
        
    wf_id = row.id
    graph = row.graph_json
    
    # 2. Apply updates to nodes
    for node in graph.get("nodes", []):
        node_id = node.get("id")
        config = node.get("data", {}).get("config", {})
        
        # Node 7: Diagnostics Specialist
        if node_id == "node_7":
            node["data"]["displayName"] = "Diagnostics Specialist (Smarter)"
            config["tools"] = DIAGNOSTIC_TOOLS
            config["systemPrompt"] = (
                "You are the Diagnostic Specialist. Your goal is to find the ROOT CAUSE of failure.\n"
                "1. Always start by calling 'ae.support.diagnose_failed_request' if a Request ID is provided.\n"
                "2. Use 'ae.agent.analyze_logs' to look for stack traces.\n"
                "3. Use 'ae.support.get_system_health' to check for infrastructure issues.\n"
                "Be concise. Summarize evidence before concluding."
            )
            
        # Node 8: Remediation Specialist
        if node_id == "node_8":
            node["data"]["displayName"] = "Remediation Specialist (HITL-Gated)"
            config["tools"] = REMEDIATION_TOOLS
            config["systemPrompt"] = (
                "You are the Remediation Specialist. You execute corrective actions.\n"
                "IMPORTANT: Restarting an agent service is a high-privilege operation. "
                "When you call 'ae.agent.restart_service', the system will automatically pause for Human Approval.\n"
                "Always check 'ae.agent.get_status' before and after actions to verify impact."
            )

        # Node 10: Default Ops Orchestrator
        if node_id == "node_10":
            config["tools"] = DEFAULT_OPS_TOOLS

    # 3. Save back to DB
    update_query = text("UPDATE workflow_definitions SET graph_json = :graph WHERE id = :id")
    session.execute(update_query, {"graph": json.dumps(graph), "id": wf_id})
    session.commit()
    print(f"Successfully updated HDFC_WF ({wf_id}) with smart tool routing and HITL prompts.")

finally:
    session.close()
