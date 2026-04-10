import type { Edge, Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";
import { EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW } from "@/lib/exampleComplexWorkflow";
import { EXAMPLE_OPERATIONS_ROUTING_WORKFLOW } from "@/lib/exampleOperationsRoutingWorkflow";

/** Gallery categories (filter tabs). */
export type TemplateCategory =
  | "customer-support"
  | "operations"
  | "research"
  | "getting-started";

export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  category: TemplateCategory;
  tags: string[];
  /** Cached count; equals graph.nodes.length */
  nodeCount: number;
  graph: { nodes: Node[]; edges: Edge[] };
}

/** Document intake → summary → human gate → bridge reply → persist. */
const DOCUMENT_REVIEW_HITL: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Document review intake",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/review/document" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 200 },
      data: {
        label: "LLM Agent",
        displayName: "Summarize & risk flags",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You review documents submitted via webhook. Summarize key points, list compliance or policy risks, " +
            "and suggest whether a human should approve before external send. Use trigger.document_text or trigger.body when present.",
          temperature: 0.25,
          maxTokens: 2048,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 520, y: 200 },
      data: {
        label: "Human Approval",
        displayName: "Legal / manager sign-off",
        nodeCategory: "action",
        config: {
          icon: "user-check",
          approvalMessage:
            "Review the LLM summary and risks. Approve to release the reply, or reject with edits in the resume payload.",
          timeout: 86400,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 780, y: 200 },
      data: {
        label: "Bridge User Reply",
        displayName: "Approved response text",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_2",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1040, y: 200 },
      data: {
        label: "Save Conversation State",
        displayName: "Persist review thread",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_2",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e1", source: "node_1", target: "node_2" },
    { id: "e2", source: "node_2", target: "node_3" },
    { id: "e3", source: "node_3", target: "node_4" },
    { id: "e4", source: "node_4", target: "node_5" },
  ],
};

/** Parallel researcher + critic → merge → synthesis. */
const MULTI_AGENT_RESEARCH: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 240 },
      data: {
        label: "Webhook Trigger",
        displayName: "Research request",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/research/query" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 280, y: 80 },
      data: {
        label: "LLM Agent",
        displayName: "Researcher",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You are a thorough researcher. Answer the user's question using trigger.message or trigger.query. " +
            "Cite assumptions; prefer structured bullets.",
          temperature: 0.4,
          maxTokens: 2048,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 280, y: 400 },
      data: {
        label: "LLM Agent",
        displayName: "Critic / fact-check",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You critique and fact-check a research draft. Input: same user question as the researcher (trigger). " +
            "List gaps, overclaims, and what to verify. Be concise.",
          temperature: 0.3,
          maxTokens: 1024,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 560, y: 240 },
      data: {
        label: "Merge",
        displayName: "Wait for both agents",
        nodeCategory: "logic",
        config: { icon: "git-merge", strategy: "waitAll" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 820, y: 240 },
      data: {
        label: "LLM Agent",
        displayName: "Synthesize final answer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "Combine node_2 (researcher) and node_3 (critic) outputs into one clear answer for the user. " +
            "Resolve disagreements; note remaining uncertainties.",
          temperature: 0.35,
          maxTokens: 2048,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e1a", source: "node_1", target: "node_2" },
    { id: "e1b", source: "node_1", target: "node_3" },
    { id: "e2", source: "node_2", target: "node_4" },
    { id: "e3", source: "node_3", target: "node_4" },
    { id: "e4", source: "node_4", target: "node_5" },
  ],
};

/** New vs returning customer branches → merge → save. */
const CUSTOMER_ONBOARDING: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Signup / login event",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/onboarding/event" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 240, y: 220 },
      data: {
        label: "Load Conversation State",
        displayName: "Load profile thread",
        nodeCategory: "action",
        config: { icon: "history", sessionIdExpression: "trigger.session_id" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 500, y: 220 },
      data: {
        label: "Condition",
        displayName: "New customer?",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'trigger.get("segment", "") == "new"',
          trueLabel: "New",
          falseLabel: "Returning",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 760, y: 80 },
      data: {
        label: "LLM Agent",
        displayName: "Welcome · first-time",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "Welcome a brand-new customer. Explain core product value, next steps, and one CTA. Use trigger.message and trigger.name if present. Short and friendly.",
          temperature: 0.6,
          maxTokens: 512,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 760, y: 360 },
      data: {
        label: "LLM Agent",
        displayName: "Welcome · returning",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "Welcome back a returning customer. Reference continuity, offer help based on trigger.message. Keep it brief.",
          temperature: 0.5,
          maxTokens: 512,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1020, y: 80 },
      data: {
        label: "Save Conversation State",
        displayName: "Save · new customer path",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_4",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 1020, y: 360 },
      data: {
        label: "Save Conversation State",
        displayName: "Save · returning path",
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
  ],
  edges: [
    { id: "e12", source: "node_1", target: "node_2" },
    { id: "e23", source: "node_2", target: "node_3" },
    {
      id: "e34",
      source: "node_3",
      target: "node_4",
      sourceHandle: "true",
      label: "Yes",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e35",
      source: "node_3",
      target: "node_5",
      sourceHandle: "false",
      label: "No",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    { id: "e46", source: "node_4", target: "node_6" },
    { id: "e57", source: "node_5", target: "node_7" },
  ],
};

/** Minimal two-node graph for first-time users. */
const GETTING_STARTED_MINIMAL: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Start here",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/hello" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 280, y: 200 },
      data: {
        label: "LLM Agent",
        displayName: "Echo assistant",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "Reply helpfully to the user. Use trigger.message or the whole trigger object as context.",
          temperature: 0.7,
          maxTokens: 512,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [{ id: "e12", source: "node_1", target: "node_2" }],
};

function asTemplate(
  t: Omit<WorkflowTemplate, "nodeCount">,
): WorkflowTemplate {
  return {
    ...t,
    nodeCount: t.graph.nodes.length,
  };
}

export const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  asTemplate({
    id: "getting-started-minimal",
    name: "Getting started",
    description:
      "Webhook trigger plus one LLM agent — the smallest runnable DAG. Use Run with a JSON trigger payload.",
    category: "getting-started",
    tags: ["starter", "minimal", "llm"],
    graph: GETTING_STARTED_MINIMAL,
  }),
  asTemplate({
    id: "it-ticket-triage",
    name: "IT ticket triage (helpdesk)",
    description:
      "Router, ForEach SLA notes, human approval on the technical path, and bridge replies for sync chat clients.",
    category: "customer-support",
    tags: ["helpdesk", "HITL", "ForEach", "router"],
    graph: EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW,
  }),
  asTemplate({
    id: "customer-onboarding",
    name: "Customer onboarding",
    description:
      "Load conversation state, branch new vs returning customers, personalized welcome, then save the turn.",
    category: "customer-support",
    tags: ["onboarding", "condition", "merge"],
    graph: CUSTOMER_ONBOARDING,
  }),
  asTemplate({
    id: "document-review-hitl",
    name: "Document review with HITL",
    description:
      "Summarize submissions with an LLM, pause for human approval, then bridge the approved reply and persist.",
    category: "operations",
    tags: ["compliance", "approval", "documents"],
    graph: DOCUMENT_REVIEW_HITL,
  }),
  asTemplate({
    id: "multi-agent-research",
    name: "Multi-agent research",
    description:
      "Run researcher and critic LLMs in parallel, merge with wait-all, then synthesize a final answer.",
    category: "research",
    tags: ["parallel", "merge", "multi-agent"],
    graph: MULTI_AGENT_RESEARCH,
  }),
  asTemplate({
    id: "ops-routing",
    name: "Ops routing",
    description:
      "LLM router to diagnostics, remediation, RCA, or default ops paths; human gate on remediation; bridge user replies for sync chat.",
    category: "operations",
    tags: ["router", "ReAct", "AIOps", "HITL"],
    graph: EXAMPLE_OPERATIONS_ROUTING_WORKFLOW,
  }),
];

export const TEMPLATE_CATEGORIES: { id: TemplateCategory; label: string }[] = [
  { id: "getting-started", label: "Getting started" },
  { id: "customer-support", label: "Customer support" },
  { id: "operations", label: "Operations" },
  { id: "research", label: "Research" },
];

export function getWorkflowTemplate(id: string): WorkflowTemplate | undefined {
  return WORKFLOW_TEMPLATES.find((t) => t.id === id);
}
