"""
Apply smart HDFC_WF configuration to the database.
- Assigns explicit tool lists to each ReAct specialist
- Adds improved system prompts with early-exit instructions
- Bumps maxIterations for diagnostics
Run this after every UI re-import of the workflow.
"""
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
    "ae.request.list_by_status",
    "ae.agent.get_status",
    "ae.agent.analyze_logs",
    "ae.support.diagnose_failed_request",
    "ae.support.get_system_health",
]

REMEDIATION_TOOLS = [
    "ae.agent.restart_service",
    "ae.agent.get_status",
    "ae.request.get_summary",
]

DEFAULT_OPS_TOOLS = [
    "ae.workflow.list",
    "ae.request.list_by_status",
    "ae.request.get_summary",
    "ae.agent.get_status",
    "ae.support.get_system_health",
]

DIAG_PROMPT = (
    "You are the Diagnostic Specialist for AutomationEdge.\n"
    "RULES:\n"
    "1. If a Request ID is given, call 'ae.support.diagnose_failed_request' FIRST.\n"
    "2. Use 'ae.agent.get_status' to check the assigned agent.\n"
    "3. Use 'ae.request.get_logs' or 'ae.agent.analyze_logs' for stack traces.\n"
    "4. STOP calling tools once you have the failure reason. Summarize findings clearly.\n"
    "5. Never repeat the same tool call with the same arguments."
)

REMED_PROMPT = (
    "You are the Remediation Specialist for AutomationEdge.\n"
    "RULES:\n"
    "1. Always call 'ae.agent.get_status' BEFORE attempting any restart.\n"
    "2. Restarting is a HIGH-PRIVILEGE operation — calling 'ae.agent.restart_service' "
    "will automatically pause for Human Approval.\n"
    "3. After actions, verify impact with 'ae.agent.get_status'.\n"
    "4. Provide a final answer immediately once the action is complete or awaiting approval."
)

OPS_PROMPT = (
    "You are the Ops Orchestrator for AutomationEdge.\n"
    "RULES:\n"
    "1. For workflow status queries, call 'ae.workflow.list' ONCE with the workflow name.\n"
    "2. For failure queries, call 'ae.request.list_by_status' ONCE.\n"
    "3. Do NOT repeat the same search with slightly different keywords.\n"
    "4. If a tool returns no results, tell the user — do not keep searching.\n"
    "5. Summarize what you found and provide a final answer within 3-4 tool calls."
)

try:
    query = text("SELECT id, graph_json FROM workflow_definitions WHERE name = 'HDFC_WF'")
    row = session.execute(query).fetchone()
    if not row:
        print("ERROR: HDFC_WF not found in database")
        exit(1)

    wf_id = row.id
    graph = row.graph_json

    changes = 0
    for node in graph.get("nodes", []):
        node_id = node.get("id")
        config = node.get("data", {}).get("config", {})

        if node_id == "node_7":
            config["tools"] = DIAGNOSTIC_TOOLS
            config["systemPrompt"] = DIAG_PROMPT
            config["maxIterations"] = 12
            node["data"]["displayName"] = "Diagnostics Specialist (Smart)"
            changes += 1

        elif node_id == "node_8":
            config["tools"] = REMEDIATION_TOOLS
            config["systemPrompt"] = REMED_PROMPT
            config["maxIterations"] = 10
            node["data"]["displayName"] = "Remediation Specialist (HITL-Gated)"
            changes += 1

        elif node_id == "node_10":
            config["tools"] = DEFAULT_OPS_TOOLS
            config["systemPrompt"] = OPS_PROMPT
            config["maxIterations"] = 8
            node["data"]["displayName"] = "Ops Orchestrator (Smart)"
            changes += 1

    update = text("UPDATE workflow_definitions SET graph_json = :graph WHERE id = :id")
    session.execute(update, {"graph": json.dumps(graph), "id": wf_id})
    session.commit()
    print(f"Updated {changes} nodes in HDFC_WF ({wf_id})")

    # Also write the updated JSON file for reference
    with open(f"hdfc_workflow_{wf_id}.json", "w") as f:
        json.dump(graph, f, indent=2)
    print(f"Wrote hdfc_workflow_{wf_id}.json")

    # Verify
    r2 = session.execute(text("SELECT graph_json FROM workflow_definitions WHERE name='HDFC_WF'")).fetchone()
    g2 = r2[0]
    for n in g2["nodes"]:
        if n["id"] in ("node_7", "node_8", "node_10"):
            t = n["data"]["config"].get("tools", [])
            print(f"  VERIFY {n['id']}: tools={len(t)} maxIter={n['data']['config'].get('maxIterations')}")

finally:
    session.close()
