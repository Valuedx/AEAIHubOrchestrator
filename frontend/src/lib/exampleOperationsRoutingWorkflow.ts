import type { Edge, Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";
import { TEMPLATE_TIER_FAST, TEMPLATE_TIER_BALANCED } from "@/lib/modelTiers";

/**
 * Operations routing workflow with four specialist paths: diagnostics,
 * remediation, RCA, and a general ops fallback.
 *
 * This DAG encodes that routing intent using **LLM Router** + **Condition** chains and
 * **Load/Save Conversation State**. Attach MCP tools on the ReAct nodes to mirror
 * specialist tool categories.
 *
 * ## External gateway / chat bridge
 *
 * Save this graph in the hub, copy the workflow UUID, and invoke it from any upstream
 * gateway or client that calls `POST /api/v1/workflows/{id}/execute`.
 *
 * Typical flow: chat gateway / scheduler / webhook -> bridge client -> `POST /execute`
 * with a merged trigger. If the client omits `message`, `session_id`, `user_id`,
 * `user_role`, `user_name`, or `user_email`, add them before calling execute so
 * downstream nodes can rely on `trigger.message` and `trigger.session_id`.
 *
 * **Async by default:** the caller may enqueue and return instance id + context URL.
 * For sync chat UX, poll `GET /context` until terminal and prefer
 * `context_json.orchestrator_user_reply`: this graph uses **Bridge User Reply** so the
 * final answer is explicit. Human-in-the-loop suspensions are not auto-resumed; use the
 * Hub **Review & Resume** UI or `POST /api/v1/workflows/{id}/callback`.
 *
 * The **Webhook Trigger** is for local **Run** in the builder; in gateway mode the same
 * fields arrive as `trigger.*` via the merged execute payload.
 *
 * Trigger JSON (Execute in UI, or equivalent merged payload):
 * {
 *   "session_id": "chat-thread-001",
 *   "message": "Workflow 2887 failed - pull the error logs and suggest a fix",
 *   "user_role": "technical",
 *   "user_id": "user@company.com",
 *   "user_name": "Jane Doe",
 *   "user_email": "jane@company.com"
 * }
 */
export const EXAMPLE_OPERATIONS_ROUTING_WORKFLOW: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 260 },
      data: {
        label: "Webhook Trigger",
        displayName: "Ops intake (webhook or gateway)",
        nodeCategory: "trigger",
        config: {
          icon: "webhook",
          method: "POST",
          path: "/ae/ops/inbound",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 220, y: 260 },
      data: {
        label: "Load Conversation State",
        displayName: "Load session history",
        nodeCategory: "action",
        config: {
          icon: "history",
          sessionIdExpression: "trigger.session_id",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 460, y: 260 },
      data: {
        label: "Intent Classifier",
        displayName: "Classify: diagnostics / remediation / RCA / ops",
        nodeCategory: "nlp",
        config: {
          icon: "target",
          utteranceExpression: "trigger.message",
          intents: [
            {
              name: "diagnostics",
              description:
                "Investigate a failure: pull status, logs, dependencies, or trace a specific incident.",
              examples: [
                "why did the payroll workflow fail last night",
                "show me logs for job abc-123",
                "what went wrong with the nightly ETL",
                "investigate the 502 errors",
              ],
              priority: 100,
            },
            {
              name: "remediation",
              description:
                "Take a corrective action: restart, retry, roll back, disable, or safely reconfigure.",
              examples: [
                "restart the workflow and clear the stuck items",
                "retry the failed job",
                "roll back to the last known good config",
                "disable the flapping schedule",
              ],
              priority: 100,
            },
            {
              name: "rca_report",
              description:
                "Produce a structured root-cause / postmortem report for an incident that already happened.",
              examples: [
                "write an RCA for yesterday's outage",
                "postmortem for the queue backlog",
                "incident report for exec review",
              ],
              priority: 100,
            },
            {
              name: "ops_orchestrator",
              description:
                "General operations guidance — workflows, queues, schedules, batch jobs — when the request isn't cleanly diagnostics/remediation/RCA.",
              examples: [
                "help me plan the migration",
                "what's the recommended pattern here",
                "review my workflow design",
              ],
              priority: 50,
            },
          ],
          mode: "hybrid",
          ...TEMPLATE_TIER_FAST,
          embeddingProvider: "openai",
          embeddingModel: "text-embedding-3-small",
          confidenceThreshold: 0.6,
          historyNodeId: "node_2",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 700, y: 260 },
      data: {
        label: "Switch",
        displayName: "Route by intent",
        nodeCategory: "logic",
        config: {
          icon: "git-fork",
          // NODES-01.a Switch replaces the earlier 3-deep Condition
          // chain. ``intents[0]`` is Intent Classifier's top-scoring
          // label; unmatched values flow through the amber default
          // handle into the ops-orchestrator fallback so no message is
          // ever dropped.
          expression: "node_3.intents[0]",
          cases: [
            { value: "diagnostics", label: "Diagnostics" },
            { value: "remediation", label: "Remediation" },
            { value: "rca_report", label: "RCA report" },
            { value: "ops_orchestrator", label: "Default ops" },
          ],
          defaultLabel: "Unknown → default ops",
          matchMode: "equals",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 980, y: 80 },
      data: {
        label: "ReAct Agent",
        displayName: "Diagnostics specialist (ReAct + tools)",
        nodeCategory: "agent",
        config: {
          icon: "repeat",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are the Diagnostic Specialist. " +
            "Investigate RPA/workflow failures: pull status, logs, dependencies, files. " +
            "Prefer MCP tools for status, logs, and diagnostics. Summarize evidence before suggesting fixes.",
          maxIterations: 12,
          // MCP hints (SMART-06): register your diagnostics/runbook
          // MCP server via the toolbar's MCP Servers dialog, then
          // list the tools this specialist should call (e.g.
          // ``get_workflow_status``, ``get_execution_logs``,
          // ``list_dependencies``). Blank ``mcpServerLabel``
          // resolves to the tenant default server. See
          // codewiki/mcp-audit.md.
          tools: [],
          mcpServerLabel: "",
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_8",
      type: "agenticNode",
      position: { x: 1180, y: 220 },
      data: {
        label: "ReAct Agent",
        displayName: "Remediation specialist (ReAct + tools)",
        nodeCategory: "agent",
        config: {
          icon: "repeat",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are the Remediation Specialist. " +
            "Execute corrective actions: restart workflows, notifications, safe config changes. " +
            "Use MCP remediation/notification tools when available. Confirm impact before destructive steps.",
          maxIterations: 12,
          // MCP hints (SMART-06): remediation tools tend to be the
          // ones flagged ``destructiveHint=true`` in the MCP spec —
          // pair this specialist with an MCP server that exposes
          // e.g. ``restart_workflow``, ``retry_failed_job``,
          // ``send_incident_notification``. The Human Approval gate
          // at node_11 holds before the remediation reply reaches
          // the user; HITL confirmation on destructive MCP calls is
          // the MCP-04 follow-up. See codewiki/mcp-audit.md.
          tools: [],
          mcpServerLabel: "",
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 1420, y: 400 },
      data: {
        label: "LLM Agent",
        displayName: "RCA report author",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          // Tier escalation: incident postmortems require synthesis
          // across sparse signals + an exec-ready narrative. The
          // balanced tier (gemini-2.5-pro) handles the reasoning
          // load; the faster flash tier tends to produce shorter,
          // more surface-level root cause analyses.
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "You are the RCA Specialist. " +
            "Produce a structured incident report: Summary, Timeline, Root cause, Impact, Prevention, " +
            "Audience (business vs technical) using trigger.user_role when present. " +
            "Use conversation and prior node outputs in context; if data is thin, say what is missing.",
          temperature: 0.35,
          maxTokens: 4096,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_10",
      type: "agenticNode",
      position: { x: 1420, y: 560 },
      data: {
        label: "LLM Agent",
        displayName: "Default ops orchestrator",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are the Ops Orchestrator. " +
            "Default handler for operations work: workflows, queues, schedules, agents, and batch jobs. " +
            "Guide investigation, tool use, and next steps. If the user needs deep logs vs fixes vs RCA, " +
            "route them to the right specialist branch.",
          temperature: 0.45,
          maxTokens: 4096,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_11",
      type: "agenticNode",
      position: { x: 1420, y: 220 },
      data: {
        label: "Human Approval",
        displayName: "Human gate: approve remediation",
        nodeCategory: "action",
        config: {
          icon: "user-check",
          approvalMessage:
            "Approve remediation actions before they are persisted to conversation history.",
          timeout: 86400,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_12",
      type: "agenticNode",
      position: { x: 1680, y: 80 },
      data: {
        label: "Save Conversation State",
        displayName: "Save turn · diagnostics answer",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_7",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_13",
      type: "agenticNode",
      position: { x: 1680, y: 220 },
      data: {
        label: "Save Conversation State",
        displayName: "Save turn · remediation (after approval)",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_8",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_14",
      type: "agenticNode",
      position: { x: 1680, y: 400 },
      data: {
        label: "Save Conversation State",
        displayName: "Save turn · RCA answer",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_9",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_15",
      type: "agenticNode",
      position: { x: 1680, y: 560 },
      data: {
        label: "Save Conversation State",
        displayName: "Save turn · default ops answer",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_10",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_16",
      type: "agenticNode",
      position: { x: 1320, y: 80 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · diagnostics",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_7",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_17",
      type: "agenticNode",
      position: { x: 1280, y: 220 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · remediation",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_8",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_18",
      type: "agenticNode",
      position: { x: 1540, y: 400 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · RCA",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_9",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_19",
      type: "agenticNode",
      position: { x: 1540, y: 560 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · default ops",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_10",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    { id: "e_3_4", source: "node_3", target: "node_4" },
    // Switch fan-out: edge.sourceHandle equals the matched intent
    // value. Unmatched cases flow through 'default' into the ops
    // orchestrator fallback so every message gets a reply.
    {
      id: "e_4_7",
      source: "node_4",
      target: "node_7",
      sourceHandle: "diagnostics",
      label: "Diagnostics",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_8",
      source: "node_4",
      target: "node_8",
      sourceHandle: "remediation",
      label: "Remediation",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_9",
      source: "node_4",
      target: "node_9",
      sourceHandle: "rca_report",
      label: "RCA",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_10",
      source: "node_4",
      target: "node_10",
      sourceHandle: "ops_orchestrator",
      label: "Default ops",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_10_default",
      source: "node_4",
      target: "node_10",
      sourceHandle: "default",
      label: "Unknown → default ops",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "4 3" },
      animated: true,
    },
    { id: "e_7_16", source: "node_7", target: "node_16" },
    { id: "e_16_12", source: "node_16", target: "node_12" },
    { id: "e_8_17", source: "node_8", target: "node_17" },
    { id: "e_17_11", source: "node_17", target: "node_11" },
    { id: "e_11_13", source: "node_11", target: "node_13" },
    { id: "e_9_18", source: "node_9", target: "node_18" },
    { id: "e_18_14", source: "node_18", target: "node_14" },
    { id: "e_10_19", source: "node_10", target: "node_19" },
    { id: "e_19_15", source: "node_19", target: "node_15" },
  ],
};
