
from sqlalchemy import create_engine, text
import json

def align_ops_routing():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    
    all_tools = [
        'ae.request.get_by_id', 'ae.request.get_status', 'ae.request.get_summary', 'ae.request.search', 
        'ae.request.list_for_user', 'ae.request.list_for_workflow', 'ae.request.list_by_status', 'ae.request.list_stuck', 
        'ae.request.list_failed_recently', 'ae.request.list_retrying', 'ae.request.list_awaiting_input', 
        'ae.request.get_input_parameters', 'ae.request.get_failure_message', 'ae.request.build_support_snapshot', 
        'ae.request.list_recent', 'ae.request.get_logs', 'ae.request.get_source_context', 'ae.request.get_time_details', 
        'ae.request.get_execution_details', 'ae.request.get_audit_logs', 'ae.request.get_step_logs', 
        'ae.request.get_live_progress', 'ae.request.get_last_error_step', 'ae.request.get_manual_intervention_context', 
        'ae.request.get_last_successful_step', 'ae.request.compare_attempts', 'ae.request.export_diagnostic_bundle', 
        'ae.request.generate_support_narrative', 'ae.request.restart', 'ae.request.restart_failed', 
        'ae.request.terminate_running', 'ae.request.resubmit_from_failure_point', 'ae.request.add_support_comment', 
        'ae.request.cancel_new_or_retry', 'ae.request.resubmit_from_start', 'ae.request.tag_case_reference', 
        'ae.request.raise_manual_handoff', 'ae.workflow.search', 'ae.workflow.list', 'ae.workflow.list_for_user', 
        'ae.workflow.get_details', 'ae.workflow.get_runtime_parameters', 'ae.workflow.get_flags', 
        'ae.workflow.get_assignment_targets', 'ae.workflow.get_permissions', 'ae.workflow.get_by_id', 
        'ae.workflow.get_recent_failure_stats', 'ae.workflow.enable', 'ae.workflow.disable', 'ae.workflow.assign_to_agent', 
        'ae.workflow.update_permissions', 'ae.workflow.rollback_version', 'ae.agent.list_stopped', 
        'ae.agent.list_unknown', 'ae.agent.get_status', 'ae.agent.get_details', 'ae.agent.get_current_load', 
        'ae.agent.get_running_requests', 'ae.agent.get_assigned_workflows', 'ae.agent.get_connectivity_state', 
        'ae.agent.get_rdp_session_state', 'ae.agent.list_running', 'ae.agent.get_recent_failures', 
        'ae.agent.get_last_heartbeat', 'ae.agent.collect_diagnostics', 'ae.agent.analyze_logs', 'ae.agent.restart_service', 
        'ae.agent.clear_stale_rdp_session', 'ae.schedule.list_all', 'ae.schedule.list_for_workflow', 
        'ae.schedule.get_details', 'ae.schedule.get_missed_runs', 'ae.schedule.get_recent_schedule_generated_requests', 
        'ae.schedule.diagnose_not_triggered', 'ae.schedule.get_next_runs', 'ae.schedule.get_last_runs', 
        'ae.schedule.enable', 'ae.schedule.run_now', 'ae.schedule.disable', 'ae.task.get_request_context', 
        'ae.task.list_blocking_requests', 'ae.task.search_pending', 'ae.task.get_assignees', 'ae.task.get_overdue', 
        'ae.task.cancel_admin', 'ae.task.reassign', 'ae.task.explain_awaiting_input', 
        'ae.credential_pool.get_availability', 'ae.credential_pool.get_waiting_requests', 
        'ae.credential_pool.diagnose_retry_state', 'ae.credential_pool.validate_for_workflow', 
        'ae.dependency.check_input_file_exists', 'ae.dependency.check_output_folder_writable', 
        'ae.dependency.run_full_preflight_for_workflow', 'ae.user.get_accessible_workflows', 
        'ae.permission.get_workflow_permissions', 'ae.permission.explain_user_access_issue', 
        'ae.platform.get_license_status', 'ae.platform.get_queue_depth', 'ae.result.get_failure_category', 
        'ae.support.diagnose_failed_request', 'ae.support.diagnose_stuck_running_request', 
        'ae.support.diagnose_retry_due_to_credentials', 'ae.support.diagnose_no_output_file', 
        'ae.support.diagnose_schedule_not_triggered', 'ae.support.diagnose_user_cannot_find_workflow', 
        'ae.support.diagnose_awaiting_input', 'ae.support.diagnose_agent_unavailable', 
        'ae.support.diagnose_rdp_blocked_workflow', 'ae.support.build_case_snapshot', 
        'ae.support.prepare_human_handoff_note', 'ae.ticket.create', 'ae.ticket.get', 'ae.ticket.close', 
        'ae.ticket.reopen', 'ae.ticket.list'
    ]

    with engine.connect() as conn:
        res = conn.execute(text("SELECT id, graph_json FROM workflow_definitions WHERE name = 'AE_Ops_Routing' ORDER BY version DESC LIMIT 1")).fetchone()
        if res:
            wf_id, graph = res
            changed = False
            for node in graph.get('nodes', []):
                if node['id'] == 'node_10':
                    if node['data'].get('label') == 'LLM Agent':
                        node['data']['label'] = 'ReAct Agent'
                        node['data']['displayName'] = 'Default ops orchestrator (ReAct + tools)'
                        node['data']['config']['tools'] = all_tools
                        node['data']['config']['maxIterations'] = 10
                        changed = True
            
            if changed:
                conn.execute(text("UPDATE workflow_definitions SET graph_json = :graph WHERE id = :id"), {"graph": json.dumps(graph), "id": wf_id})
                conn.commit()
                print(f"AE_Ops_Routing node_10 aligned successfully (ID: {wf_id})")
            else:
                print("Node 10 already aligned in AE_Ops_Routing")
        else:
            print("AE_Ops_Routing not found")

if __name__ == "__main__":
    align_ops_routing()
