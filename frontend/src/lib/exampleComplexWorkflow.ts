import type { Edge, Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";
import { TEMPLATE_TIER_FAST } from "@/lib/modelTiers";

/**
 * IT / customer helpdesk - single complete vertical (router, ForEach, HITL).
 *
 * Story: A ticket arrives (webhook **POST /support/helpdesk** in the builder, or the same
 * fields merged by an upstream gateway when it calls the execute API). The workflow loads
 * prior conversation, classifies the message, routes to (1) orders & shipping, (2) a
 * technical path with ReAct + **Human Approval**, or (3) general / deflection. Each path
 * persists the turn. In parallel, a fixed SLA checklist (**ForEach**) produces internal notes.
 *
 * For synchronous chat UX, have the upstream caller wait for terminal status and prefer
 * `context_json.orchestrator_user_reply`; **Bridge User Reply** nodes pin that text explicitly.
 * HITL resume is via the Hub or callback API.
 *
 * Example trigger payload (Execute in UI or merged payload):
 * {
 *   "session_id": "ticket-8821",
 *   "message": "VPN disconnects hourly on my Mac - case 4412",
 *   "customer_email": "alex@acme.com",
 *   "product": "Corporate VPN",
 *   "user_id": "alex@acme.com",
 *   "user_name": "Alex Rivera"
 * }
 *
 * Technical path: ReAct runs first; Human Approval pauses for L2 sign-off before save
 * (resume via Hub **Review & Resume** or callback API). Orders and general paths skip that gate.
 */
export const EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Helpdesk intake",
        nodeCategory: "trigger",
        config: {
          icon: "webhook",
          method: "POST",
          path: "/support/helpdesk",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 240, y: 220 },
      data: {
        label: "Load Conversation State",
        displayName: "Load ticket thread",
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
      position: { x: 500, y: 220 },
      data: {
        label: "Intent Classifier",
        displayName: "Classify: orders / technical / general",
        nodeCategory: "nlp",
        config: {
          icon: "target",
          utteranceExpression: "trigger.message",
          intents: [
            {
              name: "orders_and_shipping",
              description:
                "Questions about orders, billing, returns, refunds, shipment status, or tracking.",
              examples: [
                "where is my order",
                "I want a refund",
                "can you change my shipping address",
                "invoice is wrong",
              ],
              priority: 100,
            },
            {
              name: "technical_issue",
              description:
                "Product errors, crashes, login issues, outages, diagnostics — anything requiring troubleshooting.",
              examples: [
                "my app keeps crashing",
                "can't log in",
                "seeing error 502 on the dashboard",
                "VPN disconnects every hour",
              ],
              priority: 100,
            },
            {
              name: "general_inquiry",
              description:
                "Greetings, vague questions, out-of-scope messages, or general clarification requests.",
              examples: [
                "hello",
                "what can you do",
                "is there a human I can talk to",
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
      position: { x: 760, y: 220 },
      data: {
        label: "Switch",
        displayName: "Route by intent",
        nodeCategory: "logic",
        config: {
          icon: "git-fork",
          // NODES-01.a Switch replaces the earlier LLM Router + two
          // serial Condition nodes. ``intents[0]`` is Intent
          // Classifier's top-scoring label; unmatched values flow
          // through the amber default handle (hooked to the general
          // path so unknown classes still get a polite reply).
          expression: "node_3.intents[0]",
          cases: [
            { value: "orders_and_shipping", label: "Orders / shipping" },
            { value: "technical_issue", label: "Technical" },
            { value: "general_inquiry", label: "General" },
          ],
          defaultLabel: "Unknown",
          matchMode: "equals",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1020, y: 20 },
      data: {
        label: "LLM Agent",
        displayName: "Orders & billing assistant",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are a helpdesk agent for orders, billing, returns, and shipment status. " +
            "Use only the customer message and conversation history in context. " +
            "Ask for order or tracking ID if missing. Be concise, professional, and offer next steps. " +
            "If policy prevents a change, explain briefly and suggest the right channel.",
          temperature: 0.35,
          maxTokens: 1024,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 1240, y: 140 },
      data: {
        label: "ReAct Agent",
        displayName: "L1 technical support (ReAct + tools)",
        nodeCategory: "agent",
        config: {
          icon: "repeat",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are L1 IT support. Gather: product, OS/version, error text, and what changed recently. " +
            "Suggest concrete troubleshooting in order. If tools are available (MCP), use them for status or runbooks. " +
            "If severity is high (data loss, outage, security), say you are escalating and summarize for L2. " +
            "Stay within safe guidance; do not ask for passwords.",
          maxIterations: 10,
          // MCP tool hints (SMART-06): populate ``tools`` with the
          // specific names the tenant's default MCP server exposes
          // (open the MCP Servers dialog → pick a server → copy tool
          // names). Leave ``mcpServerLabel`` blank to resolve to the
          // tenant default; set it to a label if you have multiple
          // registered servers (e.g. "runbooks", "diagnostics").
          // Empty list here = ReAct calls no tools (pure reasoning
          // loop). See codewiki/mcp-audit.md for the per-tenant
          // registry and MCP-06 fingerprint-drift protections.
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
      position: { x: 1240, y: 360 },
      data: {
        label: "LLM Agent",
        displayName: "General & deflection assistant",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You handle general and out-of-scope messages: greetings, vague questions, or small talk. " +
            "Acknowledge politely, clarify how the helpdesk can help, and point to self-service or opening a ticket. " +
            "Keep replies short and on-brand.",
          temperature: 0.75,
          maxTokens: 512,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 240, y: 440 },
      data: {
        label: "ForEach",
        displayName: "Parallel SLA checklist",
        nodeCategory: "logic",
        config: {
          icon: "repeat",
          arrayExpression: `[
  {"step": "Severity & impact", "ask": "One line: who is affected and business impact"},
  {"step": "Environment", "ask": "One line: product, OS/app version, error snippet if any"},
  {"step": "Next action", "ask": "One line: recommended next step for the assignee"}
]`,
          itemVariable: "item",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_10",
      type: "agenticNode",
      position: { x: 520, y: 440 },
      data: {
        label: "LLM Agent",
        displayName: "Internal note per checklist step",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You write **internal** ticket notes for the helpdesk, not customer-facing text. " +
            "The user message includes **Current loop item** JSON with fields `step` and `ask`. " +
            "Answer only `ask` for that step in one crisp sentence, using trigger payload and prior node outputs when helpful.",
          temperature: 0.3,
          maxTokens: 200,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_11",
      type: "agenticNode",
      position: { x: 1720, y: 20 },
      data: {
        label: "Save Conversation State",
        displayName: "Save customer reply (orders path)",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_5",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_12",
      type: "agenticNode",
      position: { x: 1720, y: 140 },
      data: {
        label: "Save Conversation State",
        displayName: "Save customer reply (technical path)",
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
      position: { x: 1720, y: 360 },
      data: {
        label: "Save Conversation State",
        displayName: "Save customer reply (general path)",
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
      position: { x: 1480, y: 140 },
      data: {
        label: "Human Approval",
        displayName: "L2 review before customer send",
        nodeCategory: "action",
        config: {
          icon: "user-check",
          approvalMessage:
            "L2 review: confirm the ReAct output is safe to send to the customer (or edit via resume payload).",
          timeout: 86400,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_15",
      type: "agenticNode",
      position: { x: 1370, y: 20 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · orders",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_5",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_16",
      type: "agenticNode",
      position: { x: 1600, y: 140 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · technical",
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
      position: { x: 1480, y: 360 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · general",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_8",
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
    // Switch fan-out: edge.sourceHandle equals the matched case value.
    // Unmatched values flow through the 'default' handle into the
    // general/deflection agent — never drops a customer message.
    {
      id: "e_4_5",
      source: "node_4",
      target: "node_5",
      sourceHandle: "orders_and_shipping",
      label: "Orders / shipping",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_7",
      source: "node_4",
      target: "node_7",
      sourceHandle: "technical_issue",
      label: "Technical",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_8",
      source: "node_4",
      target: "node_8",
      sourceHandle: "general_inquiry",
      label: "General",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_8_default",
      source: "node_4",
      target: "node_8",
      sourceHandle: "default",
      label: "Unknown → general",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "4 3" },
      animated: true,
    },
    { id: "e_5_15", source: "node_5", target: "node_15" },
    { id: "e_15_11", source: "node_15", target: "node_11" },
    { id: "e_7_14", source: "node_7", target: "node_14" },
    { id: "e_14_16", source: "node_14", target: "node_16" },
    { id: "e_16_12", source: "node_16", target: "node_12" },
    { id: "e_8_17", source: "node_8", target: "node_17" },
    { id: "e_17_13", source: "node_17", target: "node_13" },
    { id: "e_1_9", source: "node_1", target: "node_9" },
    { id: "e_9_10", source: "node_9", target: "node_10" },
  ],
};

/** @deprecated Use EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW */
export const EXAMPLE_COMPLEX_WORKFLOW = EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW;
