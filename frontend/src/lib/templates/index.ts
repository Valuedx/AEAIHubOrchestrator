import type { Edge, Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";
import { EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW } from "@/lib/exampleComplexWorkflow";
import { EXAMPLE_OPERATIONS_ROUTING_WORKFLOW } from "@/lib/exampleOperationsRoutingWorkflow";
import { AE_OPS_ROUTING_TEMPLATE } from "@/lib/aeOpsRoutingTemplate";
// MODEL-01.f — tier constants live in their own module to avoid a
// circular import with the example workflow files above.
import {
  TEMPLATE_TIER_FAST,
  TEMPLATE_TIER_BALANCED,
  TEMPLATE_TIER_POWERFUL,
} from "@/lib/modelTiers";

export {
  TEMPLATE_TIER_FAST,
  TEMPLATE_TIER_BALANCED,
  TEMPLATE_TIER_POWERFUL,
};
export type { TemplateTier } from "@/lib/modelTiers";

/** Gallery categories (filter tabs). */
export type TemplateCategory =
  | "customer-support"
  | "operations"
  | "research"
  | "getting-started"
  | "notification"
  | "nlp";

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

/** Document intake → load history → summary → human gate → bridge reply → persist. */
const DOCUMENT_REVIEW_HITL: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 540,
      height: 196,
      data: {
        text:
          "GOVERNED DOCUMENT REVIEW\n" +
          "AI summarises + flags legal/policy risks, then a named approver (legal / manager) signs off before anything reaches the customer. Every submit captured by the audit log.\n" +
          "\n" +
          "For: legal · compliance · customer success · risk\n" +
          "\n" +
          "At ~50 documents/day:\n" +
          "• Initial review: ~25 min → ~2 min (BALANCED tier for legal reasoning)\n" +
          "• Approval turnaround: ~2 h → ~15 min via the pending-approvals toolbar badge\n" +
          "• Compliance trail: 100 % of submits captured (approver · reason · patch)\n" +
          "\n" +
          "Needs: Slack/email channel for the final reply · HITL approver process",
        color: "pink",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive document for review",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/review/document" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 240, y: 200 },
      data: {
        label: "Load Conversation State",
        displayName: "Load review thread",
        nodeCategory: "action",
        config: { icon: "history", sessionIdExpression: "trigger.session_id" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 500, y: 200 },
      data: {
        label: "LLM Agent",
        displayName: "Summarize doc + flag legal/policy risks",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          // Tier escalation: risk identification + compliance reasoning
          // benefits from the balanced tier (gemini-2.5-pro) over the
          // faster flash default. A missed legal/policy risk is much
          // more expensive than a few extra ms of latency.
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "You review documents submitted via webhook. Summarize key points, list compliance or policy risks, " +
            "and suggest whether a human should approve before external send. Use trigger.document_text or trigger.body when present.",
          temperature: 0.25,
          maxTokens: 2048,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 760, y: 200 },
      data: {
        label: "Human Approval",
        displayName: "Approver reviews & signs off",
        nodeCategory: "action",
        config: {
          icon: "user-check",
          // HITL-01 note: every approval submit is captured by the
          // approval_audit_log (claimed approver + reason + patch +
          // timestamp), and pending approvals appear in the toolbar
          // badge for visibility. Per-node approvers allowlist and
          // timeoutAction knobs are planned (HITL-01.c/d) and will
          // extend this config; no schema change needed for today's
          // approvers.
          approvalMessage:
            "Review the document summary and flagged risks below. " +
            "Approve to send the reply, or reject with edits in the resume payload's patch field. " +
            "Your claimed approver name is captured for the audit trail.",
          // 4 hours — realistic for legal/manager review without
          // letting stale approvals pile up. The pending-approvals
          // badge (HITL-01.b) shows the full queue regardless.
          timeout: 14400,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1020, y: 200 },
      data: {
        label: "Bridge User Reply",
        displayName: "Approved response text",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_3",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1280, y: 200 },
      data: {
        label: "Save Conversation State",
        displayName: "Persist review thread",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_3",
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
    { id: "e5", source: "node_5", target: "node_6" },
  ],
};

/** Parallel researcher + critic → merge → synthesis. */
const MULTI_AGENT_RESEARCH: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 540,
      height: 180,
      data: {
        text:
          "MULTI-AGENT RESEARCH — PARALLEL RESEARCHER + CRITIC\n" +
          "Researcher drafts an answer while a Critic independently lists gaps + overclaims — in parallel. A BALANCED-tier Synthesizer reconciles them into one clean answer.\n" +
          "\n" +
          "For: analyst desks · product research · competitive intel · exec briefings\n" +
          "\n" +
          "At ~50 questions/day:\n" +
          "• Draft-then-review time: ~45 min → ~30 s end-to-end\n" +
          "• Fact-check coverage: 100 % (parallel path, not optional)\n" +
          "\n" +
          "Needs: a good research prompt — consider pairing with a KB or A2A researcher",
        color: "yellow",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 240 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a research question",
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
        displayName: "Draft a research answer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are a thorough researcher. Answer the user's question using trigger.message or trigger.query. " +
            "Cite assumptions; prefer structured bullets.",
          temperature: 0.4,
          maxTokens: 2048,
          memoryEnabled: false,
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
        displayName: "Critique & fact-check (in parallel)",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You critique and fact-check a research draft. Input: same user question as the researcher (trigger). " +
            "List gaps, overclaims, and what to verify. Be concise.",
          temperature: 0.3,
          maxTokens: 1024,
          memoryEnabled: false,
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
        displayName: "Wait for both before synthesising",
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
        displayName: "Synthesise a single final answer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          // Tier escalation: the synthesizer resolves conflicting
          // claims from researcher + critic — that's reasoning-heavy,
          // which is what the balanced tier (gemini-2.5-pro) is
          // optimised for. Researcher + critic themselves stay on
          // FAST to keep the parallel fan-out cheap.
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "Combine node_2 (researcher) and node_3 (critic) outputs into one clear answer for the user. " +
            "Resolve disagreements; note remaining uncertainties.",
          temperature: 0.35,
          maxTokens: 2048,
          memoryEnabled: false,
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
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -200 },
      width: 540,
      height: 170,
      data: {
        text:
          "PERSONALISED WELCOMES — NEW VS RETURNING\n" +
          "Signup / login event triggers a Condition branch on customer segment, a tailored LLM welcome for each path, and persistence so the next visit picks up the thread.\n" +
          "\n" +
          "For: growth · product-led onboarding · customer success\n" +
          "\n" +
          "At ~500 events/day:\n" +
          "• Hand-crafted welcome template maintenance time: 0 (one prompt covers both paths)\n" +
          "• Activation lift from personalisation: typically +15–25% vs generic welcomes",
        color: "blue",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Signup / login event arrives",
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
        displayName: "First time with us?",
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
        displayName: "Welcome a brand-new customer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "Welcome a brand-new customer. Explain core product value, next steps, and one CTA. Use trigger.message and trigger.name if present. Short and friendly.",
          temperature: 0.6,
          maxTokens: 512,
          historyNodeId: "node_2",
          memoryEnabled: true,
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
        displayName: "Welcome back a returning customer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "Welcome back a returning customer. Reference continuity, offer help based on trigger.message. Keep it brief.",
          temperature: 0.5,
          maxTokens: 512,
          historyNodeId: "node_2",
          memoryEnabled: true,
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
        displayName: "Save the new-customer turn",
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
        displayName: "Save the returning-customer turn",
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
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -160 },
      width: 520,
      height: 130,
      data: {
        text:
          "START HERE — YOUR FIRST AGENT\n" +
          "The smallest runnable DAG: an HTTP webhook hands the message to one LLM agent, which replies. Load it, click Run with a JSON payload like {\"message\":\"hi\"}, and you're live.\n" +
          "\n" +
          "Next step: add Load/Save Conversation State for multi-turn memory, or pick a richer template from the gallery.",
        color: "grey",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a message (HTTP POST)",
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
        displayName: "Reply with a helpful answer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
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

/** RAG knowledge base Q&A: retrieve chunks then answer with grounded context. */
const RAG_KNOWLEDGE_QA: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 540,
      height: 196,
      data: {
        text:
          "GROUNDED Q&A ON YOUR KNOWLEDGE BASE\n" +
          "Fetches the top-K relevant chunks from your docs / KB then answers with cited context + multi-turn memory. Swap the KB's embedding to gemini-embedding-2 for mixed-media corpora (text + images + PDFs + audio).\n" +
          "\n" +
          "For: customer support · internal help-desk · sales enablement\n" +
          "\n" +
          "At ~500 questions/day:\n" +
          "• Answer latency: ~3–8 s end-to-end (topK=5, scoreThreshold=0.3)\n" +
          "• Agent deflection: ~40–60 % of tier-1 questions resolved without escalation\n" +
          "• Citation coverage: 100 % (answers cite the chunk ids retrieved)\n" +
          "\n" +
          "Needs: at least one populated Knowledge Base · embedding provider configured",
        color: "yellow",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a question",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/kb/ask" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 240, y: 200 },
      data: {
        label: "Load Conversation State",
        displayName: "Load session history",
        nodeCategory: "action",
        config: { icon: "history", sessionIdExpression: "trigger.session_id" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 500, y: 200 },
      data: {
        label: "Knowledge Retrieval",
        displayName: "Find relevant passages in the KB",
        nodeCategory: "knowledge",
        config: {
          icon: "database",
          // knowledgeBaseIds: attach one or more KBs in the
          // inspector. The KB's own embedding_model drives retrieval
          // — pick ``gemini-embedding-2`` at KB-create time for
          // MULTIMODAL corpora (text + image + video + audio, 3072d
          // Matryoshka). Stick with OpenAI ``text-embedding-3-small``
          // (1536d) for text-only KBs that need the cheapest option.
          // See codewiki/rag-knowledge-base.md for the full embedding
          // picker matrix.
          knowledgeBaseIds: [],
          queryExpression: "trigger.message",
          topK: 5,
          scoreThreshold: 0.3,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 760, y: 200 },
      data: {
        label: "LLM Agent",
        displayName: "Answer the question using those passages",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are a knowledgeable assistant. Answer the user's question using ONLY the retrieved context below.\n\n" +
            "Retrieved context:\n{{ node_3.context_text }}\n\n" +
            "If the context does not contain enough information, say so clearly rather than guessing. " +
            "Cite the source chunks when available.",
          temperature: 0.2,
          maxTokens: 2048,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1020, y: 200 },
      data: {
        label: "Bridge User Reply",
        displayName: "Return grounded answer",
        nodeCategory: "action",
        config: { icon: "message-square", responseNodeId: "node_4", messageExpression: "" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1280, y: 200 },
      data: {
        label: "Save Conversation State",
        displayName: "Persist Q&A turn",
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
  ],
  edges: [
    { id: "e12", source: "node_1", target: "node_2" },
    { id: "e23", source: "node_2", target: "node_3" },
    { id: "e34", source: "node_3", target: "node_4" },
    { id: "e45", source: "node_4", target: "node_5" },
    { id: "e56", source: "node_5", target: "node_6" },
  ],
};

/** Schedule Trigger → LLM summary → Slack notification. */
const SCHEDULED_NOTIFICATION: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -170 },
      width: 500,
      height: 150,
      data: {
        text:
          "DAILY BRIEFING — SCHEDULED DIGEST TO SLACK\n" +
          "Cron fires weekdays at 9 AM UTC, an LLM summarises the day's brief, and a Slack notification posts it to the team channel.\n" +
          "\n" +
          "For: team huddles · exec briefings · status updates\n" +
          "• Swap the LLM prompt for your context (yesterday's commits, metrics, tickets)\n" +
          "• Retarget by changing cron or adding a Switch to multi-team-alias destinations",
        color: "grey",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Schedule Trigger",
        displayName: "Fire weekdays at 9 AM UTC",
        nodeCategory: "trigger",
        config: { icon: "clock", cron: "0 9 * * 1-5" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 280, y: 200 },
      data: {
        label: "LLM Agent",
        displayName: "Compose daily report",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You write a concise daily operations digest for the engineering team. " +
            "Summarize: top open incidents, upcoming scheduled jobs, and any anomalies from context. " +
            "Format with bullet points, keep it under 300 words. Today is {{ trigger.timestamp | default('today') }}.",
          temperature: 0.3,
          maxTokens: 1024,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 200 },
      data: {
        label: "Notification",
        displayName: "Post the digest to Slack",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_WEBHOOK_URL }}",
          messageTemplate: "{{ node_2.response }}",
          username: "Daily Digest Bot",
          iconEmoji: ":newspaper:",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e12", source: "node_1", target: "node_2" },
    { id: "e23", source: "node_2", target: "node_3" },
  ],
};

/** NLP: hybrid intent classifier → entity extractor → branch to specialist agent. */
const NLP_INTENT_ENTITY: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 560,
      height: 180,
      data: {
        text:
          "STRUCTURED INTENT + SLOT ROUTING\n" +
          "Hybrid Intent Classifier (lexical + embedding + LLM fallback) figures out what the user wants; an Entity Extractor pulls the slots (dates, reference IDs) scoped by intent; specialist agents handle each path.\n" +
          "\n" +
          "For: bookings · status checks · cancellations · self-service chat\n" +
          "\n" +
          "At ~800 messages/day:\n" +
          "• Classification latency: ~80 ms (heuristic) / ~700 ms (LLM fallback)\n" +
          "• Slot-filling accuracy: ~95 % with hybrid mode + LLM fallback on misses\n" +
          "• Production-grade upgrade over plain LLM Router when you need confidence scores + slots",
        color: "purple",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 240 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a chat message",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/chat/message" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 240, y: 240 },
      data: {
        label: "Load Conversation State",
        displayName: "Load session history",
        nodeCategory: "action",
        config: { icon: "history", sessionIdExpression: "trigger.session_id" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 490, y: 240 },
      data: {
        label: "Intent Classifier",
        displayName: "Classify user intent",
        nodeCategory: "nlp",
        config: {
          icon: "target",
          utteranceExpression: "trigger.message",
          intents: [
            {
              name: "book_appointment",
              description: "User wants to schedule or book an appointment",
              examples: ["book a meeting", "schedule a call", "I need an appointment"],
              priority: 100,
            },
            {
              name: "check_status",
              description: "User asks about the status of an order, ticket, or request",
              examples: ["where is my order", "what is the status", "any updates on my ticket"],
              priority: 100,
            },
            {
              name: "cancel_request",
              description: "User wants to cancel a booking, order, or subscription",
              examples: ["cancel my order", "I want to unsubscribe", "please cancel"],
              priority: 100,
            },
            {
              name: "general_inquiry",
              description: "General questions or small talk",
              examples: ["hello", "what can you do", "help"],
              priority: 50,
            },
          ],
          mode: "hybrid",
          ...TEMPLATE_TIER_FAST,
          confidenceThreshold: 0.6,
          historyNodeId: "node_2",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 760, y: 240 },
      data: {
        label: "Entity Extractor",
        displayName: "Pull slots (dates, ref IDs)",
        nodeCategory: "nlp",
        config: {
          icon: "list-filter",
          sourceExpression: "trigger.message",
          entities: [
            {
              name: "date",
              type: "date",
              description: "The date mentioned by the user",
              required: false,
            },
            {
              name: "reference_id",
              type: "regex",
              pattern: "[A-Z]{2,4}-?\\d{4,8}",
              description: "Order, ticket, or booking ID",
              required: false,
            },
          ],
          scopeFromNode: "node_3",
          intentEntityMapping: {
            book_appointment: ["date"],
            check_status: ["reference_id"],
            cancel_request: ["reference_id"],
          },
          llmFallback: true,
          ...TEMPLATE_TIER_FAST,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1020, y: 240 },
      data: {
        label: "Condition",
        displayName: "Is this a booking?",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'node_3.intent == "book_appointment"',
          trueLabel: "Book",
          falseLabel: "Other",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1280, y: 80 },
      data: {
        label: "LLM Agent",
        displayName: "Handle the booking",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are a booking assistant. The user wants to schedule an appointment. " +
            "Extracted entities: {{ node_4.entities | tojson }}. " +
            "Confirm the date, ask for missing info, and confirm the booking. Be friendly and concise.",
          temperature: 0.4,
          maxTokens: 512,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 1280, y: 380 },
      data: {
        label: "LLM Agent",
        displayName: "General intent handler",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are a helpful assistant. The classified intent is '{{ node_3.intent }}' " +
            "with entities: {{ node_4.entities | tojson }}. " +
            "Handle the user's request appropriately — check status, process a cancellation, or answer a general inquiry.",
          temperature: 0.5,
          maxTokens: 1024,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_8",
      type: "agenticNode",
      position: { x: 1560, y: 80 },
      data: {
        label: "Save Conversation State",
        displayName: "Save booking turn",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_6",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 1560, y: 380 },
      data: {
        label: "Save Conversation State",
        displayName: "Save general turn",
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
  ],
  edges: [
    { id: "e12", source: "node_1", target: "node_2" },
    { id: "e23", source: "node_2", target: "node_3" },
    { id: "e34", source: "node_3", target: "node_4" },
    { id: "e45", source: "node_4", target: "node_5" },
    {
      id: "e56",
      source: "node_5",
      target: "node_6",
      sourceHandle: "true",
      label: "Book",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e57",
      source: "node_5",
      target: "node_7",
      sourceHandle: "false",
      label: "Other",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    { id: "e68", source: "node_6", target: "node_8" },
    { id: "e79", source: "node_7", target: "node_9" },
  ],
};

/** Conversational support chatbot with episode archiving on issue resolution. */
const EPISODE_ARCHIVE_SUPPORT: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 540,
      height: 180,
      data: {
        text:
          "SUPPORT CHAT WITH MEMORY — ARCHIVE RESOLVED ISSUES\n" +
          "Multi-turn support assistant that notices when the user marks an issue as resolved, archives that episode into episodic memory (so next visit starts clean), and otherwise keeps the conversation going.\n" +
          "\n" +
          "For: customer support · long-running case management · per-issue context\n" +
          "\n" +
          "At ~300 active chats/day:\n" +
          "• Episode archive triggers automatically on resolved intent (no manual end button)\n" +
          "• Memory hygiene: last-N active-episode context · closed episodes searchable via semantic recall\n" +
          "• Reduces prompt-context size ~40% for long-running conversations",
        color: "blue",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 260 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a support message",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/support/chat" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 240, y: 260 },
      data: {
        label: "Load Conversation State",
        displayName: "Load support thread",
        nodeCategory: "action",
        config: { icon: "history", sessionIdExpression: "trigger.session_id" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 490, y: 260 },
      data: {
        label: "LLM Router",
        displayName: "Detect resolution signal",
        nodeCategory: "agent",
        config: {
          icon: "route",
          ...TEMPLATE_TIER_FAST,
          intents: ["issue_open", "issue_resolved"],
          historyNodeId: "node_2",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 740, y: 260 },
      data: {
        label: "Condition",
        displayName: "Issue resolved?",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'node_3.intent == "issue_resolved"',
          trueLabel: "Resolved",
          falseLabel: "Ongoing",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1000, y: 80 },
      data: {
        label: "LLM Agent",
        displayName: "Closing confirmation",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "The user has indicated their issue is resolved. " +
            "Write a warm closing message: confirm the resolution, offer to re-open if needed, and thank them. " +
            "Keep it to 2–3 sentences.",
          temperature: 0.5,
          maxTokens: 256,
          historyNodeId: "node_2",
          memoryEnabled: true,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1000, y: 400 },
      data: {
        label: "LLM Agent",
        displayName: "Support assistant",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are a friendly support agent. Help the user troubleshoot their issue. " +
            "Use prior conversation history and trigger.message for context. " +
            "Ask for one piece of clarification at a time if needed. Be concise and solution-focused.",
          temperature: 0.4,
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
      position: { x: 1260, y: 80 },
      data: {
        label: "Archive Active Episode",
        displayName: "Archive resolved issue",
        nodeCategory: "action",
        config: {
          icon: "archive",
          sessionIdExpression: "trigger.session_id",
          summaryExpression: "",
          titleExpression: "",
          reason: "resolved",
          memoryProfileId: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_8",
      type: "agenticNode",
      position: { x: 1260, y: 400 },
      data: {
        label: "Bridge User Reply",
        displayName: "Return support reply",
        nodeCategory: "action",
        config: { icon: "message-square", responseNodeId: "node_6", messageExpression: "" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 1520, y: 400 },
      data: {
        label: "Save Conversation State",
        displayName: "Persist support turn",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_6",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e12", source: "node_1", target: "node_2" },
    { id: "e23", source: "node_2", target: "node_3" },
    { id: "e34", source: "node_3", target: "node_4" },
    {
      id: "e45",
      source: "node_4",
      target: "node_5",
      sourceHandle: "true",
      label: "Resolved",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e46",
      source: "node_4",
      target: "node_6",
      sourceHandle: "false",
      label: "Ongoing",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    { id: "e57", source: "node_5", target: "node_7" },
    { id: "e68", source: "node_6", target: "node_8" },
    { id: "e89", source: "node_8", target: "node_9" },
  ],
};

// ---------------------------------------------------------------------------
// NODES-01 showcase templates — demonstrate the logic primitives shipped
// in the 2026 spring sprint (Switch, While, CYCLIC-01 loopback edges).
// Each is intentionally small so the pattern reads at a glance.
// ---------------------------------------------------------------------------

/** Priority routing: webhook → Switch → per-tier notification. Demonstrates
 *  NODES-01.a with no upstream classifier — the Switch reads directly from
 *  the trigger payload's `priority` field. ``matchMode: "equals_ci"`` keeps
 *  it robust to `"P1"` vs `"p1"`. */
const PRIORITY_ROUTING_SWITCH: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -180 },
      width: 520,
      height: 160,
      data: {
        text:
          "WHEN TO USE THIS PATTERN — SWITCH FROM A STRUCTURED FIELD\n" +
          "Reach for Switch (not Condition chains) when the payload ALREADY tells you where to go — priority, plan tier, channel, country code. No classifier needed.\n" +
          "\n" +
          "Production impact at 500 events/day:\n" +
          "• Routing latency: ~2 s (classifier path) → ~10 ms (direct Switch)\n" +
          "• Mis-routes: ~0 — matchMode=equals_ci handles 'p1' vs 'P1'\n" +
          "• Amber default handle guarantees no event falls through the cracks",
        color: "purple",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Incident intake",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/incidents/new" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 280, y: 220 },
      data: {
        label: "Switch",
        displayName: "Route by priority tier",
        nodeCategory: "logic",
        config: {
          icon: "git-fork",
          // NODES-01.a Switch — no classifier needed when the payload
          // already carries a structured field. equals_ci so a
          // caller sending "p1" matches case value "P1".
          expression: "trigger.priority",
          cases: [
            { value: "P1", label: "P1 · page oncall" },
            { value: "P2", label: "P2 · Slack high-pri" },
            { value: "P3", label: "P3 · Slack standard" },
            { value: "P4", label: "P4 · email" },
          ],
          defaultLabel: "Unknown → email",
          matchMode: "equals_ci",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 620, y: 40 },
      data: {
        label: "Notification",
        displayName: "PagerDuty page (P1)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "pagerduty",
          destination: "{{ env.PAGERDUTY_ROUTING_KEY }}",
          severity: "critical",
          eventAction: "trigger",
          messageTemplate:
            "[P1] {{ trigger.title }} — {{ trigger.description }} ({{ trigger.service }})",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 620, y: 180 },
      data: {
        label: "Notification",
        displayName: "Slack high-pri (P2)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_INCIDENTS_WEBHOOK }}",
          username: "Incident Bot",
          iconEmoji: ":rotating_light:",
          messageTemplate:
            ":rotating_light: *P2* — *{{ trigger.title }}*\n{{ trigger.description }}\nService: {{ trigger.service }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 620, y: 320 },
      data: {
        label: "Notification",
        displayName: "Slack standard (P3)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_OPS_WEBHOOK }}",
          username: "Ops Bot",
          iconEmoji: ":mag:",
          messageTemplate:
            ":mag: P3 — {{ trigger.title }} ({{ trigger.service }})",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 620, y: 460 },
      data: {
        label: "Notification",
        displayName: "Email digest (P4 / default)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "email",
          destination: "{{ env.OPS_DIGEST_EMAIL }}",
          emailProvider: "sendgrid",
          subject: "P4 ticket filed: {{ trigger.title }}",
          messageTemplate:
            "P4 ticket filed.\n\nTitle: {{ trigger.title }}\nService: {{ trigger.service }}\nDescription: {{ trigger.description }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    {
      id: "e_2_3",
      source: "node_2",
      target: "node_3",
      sourceHandle: "P1",
      label: "P1",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_2_4",
      source: "node_2",
      target: "node_4",
      sourceHandle: "P2",
      label: "P2",
      style: { stroke: "#f97316", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_2_5",
      source: "node_2",
      target: "node_5",
      sourceHandle: "P3",
      label: "P3",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_2_6",
      source: "node_2",
      target: "node_6",
      sourceHandle: "P4",
      label: "P4",
      style: { stroke: "#64748b", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_2_6_default",
      source: "node_2",
      target: "node_6",
      sourceHandle: "default",
      label: "Unknown → email",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "4 3" },
      animated: true,
    },
    { id: "e_1_2", source: "node_1", target: "node_2" },
  ],
};

/** Retry-until-success: webhook → While → HTTP Request → Notification.
 *  Demonstrates NODES-01.b — the body node (HTTP Request) re-executes
 *  each iteration; the loop exits the moment the response is 2xx OR the
 *  iteration cap hits. `_loop_index` is available in the condition for
 *  ``do at least N tries`` style expressions. */
const RETRY_UNTIL_SUCCESS_WHILE: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -180 },
      width: 520,
      height: 160,
      data: {
        text:
          "WHEN TO USE THIS PATTERN — WHILE LOOP WITH HARD CAP\n" +
          "Flaky upstream? Use While to keep calling until success OR the iteration cap hits. _loop_index is available inside the condition for 'at-least-N-tries' style guards.\n" +
          "\n" +
          "Production impact on a flaky upstream (~5% transient-failure rate):\n" +
          "• Single-attempt success: ~95 % → retry-until-success: ~99.99 % (≤5 tries)\n" +
          "• Runaway protection: hard cap 5 enforced by engine\n" +
          "• Ideal for: provisioning calls · webhook deliveries · third-party APIs",
        color: "purple",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Start the retry flow",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/flaky-api/retry" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 200 },
      data: {
        label: "While",
        displayName: "Retry while non-2xx",
        nodeCategory: "logic",
        config: {
          icon: "rotate-cw",
          // NODES-01.b While — ``_loop_index`` is 0 on the first pass
          // (before any body node has run), so short-circuit that
          // case via the index guard. Once node_3 exists in context,
          // the status_code check takes over.
          condition:
            "_loop_index == 0 or node_3.status_code < 200 or node_3.status_code >= 300",
          maxIterations: 5,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 520, y: 200 },
      data: {
        label: "HTTP Request",
        displayName: "Call upstream API",
        nodeCategory: "action",
        config: {
          icon: "globe",
          url: "{{ trigger.target_url }}",
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: "Bearer {{ env.UPSTREAM_API_TOKEN }}",
          },
          body: "{{ trigger.payload | tojson }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 820, y: 200 },
      data: {
        label: "Notification",
        displayName: "Report outcome",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_OPS_WEBHOOK }}",
          username: "Retry Bot",
          iconEmoji: ":arrows_counterclockwise:",
          // Reports the final HTTP state + number of attempts the
          // While loop made. node_3 is the last-iteration's output.
          messageTemplate:
            "Retry complete. Final status: `{{ node_3.status_code }}` after attempt(s). Target: {{ trigger.target_url }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    { id: "e_3_4", source: "node_3", target: "node_4" },
  ],
};

/** Agent ↔ tool loopback: webhook → planner LLM → Condition (need a tool?) →
 *  either MCP Tool (loops back to planner) OR final-response LLM. Demonstrates
 *  CYCLIC-01 — a loopback edge on the tool's output brings control back to
 *  the planner so the next iteration runs with the tool result in context.
 *  Explicit node-level version of what ReAct Agent does internally; useful
 *  when you want per-step observability, data pins, or custom gating. */
const AGENT_TOOL_LOOPBACK: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -180 },
      width: 540,
      height: 160,
      data: {
        text:
          "WHEN TO USE THIS PATTERN — EXPLICIT AGENT + TOOL LOOP\n" +
          "Pick this over a bare ReAct Agent when you want per-step observability, data pins, or custom gating. Each planner turn and each tool call is a first-class node you can inspect + pin.\n" +
          "\n" +
          "Production reasons to reach for this shape:\n" +
          "• Regulated flows: every tool call must be auditable in ExecutionLogs\n" +
          "• Tool call requires its own HITL gate before firing\n" +
          "• Hard per-call max-iterations cap enforced by engine (5 here)",
        color: "purple",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 200 },
      data: {
        label: "Webhook Trigger",
        displayName: "Agent task intake",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/agent-loop/start" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 200 },
      data: {
        label: "LLM Agent",
        displayName: "Planner · decide next action",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "You are a planning agent. Each turn, decide the single best next step. " +
            "Respond as STRICT JSON: " +
            '{"action": "use_tool" | "done", "tool_name": "string", "tool_args": {}, "final_answer": "string"}. ' +
            "When action=use_tool, include tool_name + tool_args. " +
            "When action=done, include final_answer and set tool_name/tool_args to null. " +
            "Use prior tool results in context (node_4.result on subsequent loops) to refine your plan.",
          temperature: 0.2,
          maxTokens: 1024,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 200 },
      data: {
        label: "Condition",
        displayName: "Planner said use_tool?",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'node_2.action == "use_tool"',
          trueLabel: "Call tool",
          falseLabel: "Done",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 820, y: 100 },
      data: {
        label: "MCP Tool",
        displayName: "Execute planner's tool",
        nodeCategory: "action",
        config: {
          icon: "wrench",
          // Tool name + args come from the planner's JSON output. The
          // MCP server resolver uses the tenant's default registry
          // row when ``mcpServerLabel`` is blank (see MCP-02).
          toolName: "{{ node_2.tool_name }}",
          arguments: "{{ node_2.tool_args | tojson }}",
          mcpServerLabel: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 820, y: 320 },
      data: {
        label: "LLM Agent",
        displayName: "Final response",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "The planner is done. Echo ``node_2.final_answer`` as a polished, user-facing reply. " +
            "If final_answer is missing, explain what information was gathered and what's still open.",
          temperature: 0.4,
          maxTokens: 1024,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    {
      id: "e_3_4",
      source: "node_3",
      target: "node_4",
      sourceHandle: "true",
      label: "Use tool",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_5",
      source: "node_3",
      target: "node_5",
      sourceHandle: "false",
      label: "Done",
      style: { stroke: "#0ea5e9", strokeWidth: 2 },
      animated: true,
    },
    // CYCLIC-01 loopback: after the tool runs, control flows BACK to
    // the planner so the next iteration sees node_4.result. Hard cap
    // of 5 stops runaway loops; planner can end earlier by setting
    // ``action: "done"``.
    {
      id: "e_4_2_loopback",
      source: "node_4",
      target: "node_2",
      type: "loopback",
      data: { maxIterations: 5 },
      label: "↻ replan with tool result",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "6 4" },
      animated: true,
    },
  ],
};


// ---------------------------------------------------------------------------
// AutomationEdge (RPA) example templates — TMPL-02.
//
// The AE node submits a workflow to an AutomationEdge engine and then
// SUSPENDS the parent workflow until AE reports a terminal state. Poll
// mode (default) has the Beat task re-check every ``pollIntervalSeconds``;
// webhook mode lets AE call back via an AE HTTP step. Either way, the
// orchestrator resumes downstream nodes with AE's output payload on
// node_X.result so the surrounding graph can react to success/failure.
// See ``codewiki/automationedge.md`` for the full wire format.
// ---------------------------------------------------------------------------

/** Invoice intake → ERP via AE.
 *
 * Email/webhook drops an invoice payload → an LLM-backed Entity
 * Extractor pulls the structured fields → AE submits those fields to
 * a back-office ERP workflow → on AE completion a Condition branches
 * on the reported status to either confirm (Slack) or escalate to
 * finance ops (email). Showcases the "data in → RPA → data out"
 * pattern with a strong post-AE branch for visibility into the
 * automation's outcome. */
const INVOICE_AE_ERP: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 540,
      height: 196,
      data: {
        text:
          "INVOICE AUTO-POST TO ERP (RPA)\n" +
          "Entity Extractor pulls invoice fields (number, amount, vendor, due date), AutomationEdge posts them to the ERP workflow, and a post-AE branch confirms to Slack or escalates failures to finance ops by email.\n" +
          "\n" +
          "For: accounts-payable · finance ops · controllership\n" +
          "\n" +
          "At ~200 invoices/day:\n" +
          "• Keying time: ~4 min/invoice → ~20 s  (saves ~12 AP-hours/day)\n" +
          "• Posting errors: ~5 % → ~1 % (back-office workflow runs unchanged)\n" +
          "• Exception handling: failures auto-escalated with full AE request id trail\n" +
          "\n" +
          "Needs: AutomationEdge integration configured · ERP workflow name · Slack/email destinations",
        color: "green",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive invoice payload",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/ap/invoice" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 220 },
      data: {
        label: "Entity Extractor",
        displayName: "Pull fields from the invoice",
        nodeCategory: "nlp",
        config: {
          icon: "list-filter",
          sourceExpression: "trigger.raw_text",
          entities: [
            {
              name: "invoice_number",
              type: "regex",
              pattern: "INV[-_]?\\d{4,10}",
              description: "Invoice number (INV-prefix).",
              required: true,
            },
            {
              name: "amount",
              type: "number",
              description: "Total amount due.",
              required: true,
            },
            {
              name: "vendor",
              type: "free_text",
              description: "Vendor / supplier name.",
              required: true,
            },
            {
              name: "due_date",
              type: "date",
              description: "Invoice due date (ISO 8601 preferred).",
              required: false,
            },
          ],
          // LLM fallback fills in required entities that the rule-
          // based extractors missed — useful on noisier invoices.
          llmFallback: true,
          ...TEMPLATE_TIER_FAST,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 220 },
      data: {
        label: "AutomationEdge",
        displayName: "Post the invoice to the ERP (RPA)",
        nodeCategory: "action",
        config: {
          icon: "bot",
          // Blank integrationLabel → uses the tenant's default
          // AutomationEdge integration (toolbar ⇢ Integrations dialog).
          integrationLabel: "",
          workflowName: "AP_Invoice_Post_v2",
          authMode: "ae_session",
          credentialsSecretPrefix: "AUTOMATIONEDGE",
          // Input mapping: each entry's valueExpression is safe_eval'd
          // against the workflow context. Entity Extractor output lives
          // at node_2.entities.{name} (array of matches).
          inputMapping: [
            {
              name: "invoice_number",
              type: "string",
              valueExpression: "node_2.entities.invoice_number[0]",
            },
            {
              name: "amount",
              type: "number",
              valueExpression: "node_2.entities.amount[0]",
            },
            {
              name: "vendor",
              type: "string",
              valueExpression: "node_2.entities.vendor[0]",
            },
            {
              name: "due_date",
              type: "string",
              valueExpression: "node_2.entities.due_date[0]",
            },
            {
              name: "source_message_id",
              type: "string",
              valueExpression: "trigger.message_id",
            },
          ],
          completionMode: "poll",
          pollIntervalSeconds: 30,
          timeoutSeconds: 3600,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 860, y: 220 },
      data: {
        label: "Condition",
        displayName: "Did the ERP accept it?",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          // AE reports its terminal state + payload on
          // node_3.result.{...}; status == "success" indicates the
          // RPA workflow ended in its happy path.
          condition: 'node_3.result.status == "success"',
          trueLabel: "Success",
          falseLabel: "Failure",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1160, y: 100 },
      data: {
        label: "Notification",
        displayName: "Confirm posting to AP team (Slack)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_AP_WEBHOOK }}",
          username: "Invoice Bot",
          iconEmoji: ":receipt:",
          messageTemplate:
            ":receipt: Invoice *{{ node_2.entities.invoice_number[0] }}* from *{{ node_2.entities.vendor[0] }}* (${{ node_2.entities.amount[0] }}) posted to ERP. AE job `{{ node_3.request_id }}` completed in {{ node_3.result.elapsed_seconds }}s.",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1160, y: 340 },
      data: {
        label: "Notification",
        displayName: "Escalate failure to finance ops (email)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "email",
          destination: "{{ env.FINANCE_OPS_EMAIL }}",
          emailProvider: "sendgrid",
          subject:
            "Invoice posting failed: {{ node_2.entities.invoice_number[0] }} ({{ node_2.entities.vendor[0] }})",
          messageTemplate:
            "The AutomationEdge workflow for invoice {{ node_2.entities.invoice_number[0] }} ended in status `{{ node_3.result.status }}`.\n\nVendor: {{ node_2.entities.vendor[0] }}\nAmount: {{ node_2.entities.amount[0] }}\nAE request id: {{ node_3.request_id }}\nError detail: {{ node_3.result.error | default('n/a') }}\n\nRetry or post manually in the ERP.",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    { id: "e_3_4", source: "node_3", target: "node_4" },
    {
      id: "e_4_5",
      source: "node_4",
      target: "node_5",
      sourceHandle: "true",
      label: "Success",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_6",
      source: "node_4",
      target: "node_6",
      sourceHandle: "false",
      label: "Failure",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
  ],
};

/** Incident auto-remediation via AE.
 *
 * Alert webhook → Intent Classifier categorises the incident →
 * Switch routes to one of {auto-remediation, investigation, manual}.
 * The auto-remediation path gates Human Approval BEFORE the AE RPA
 * workflow runs (destructive actions need governance), then a
 * synthesiser LLM narrates the outcome for the incident channel.
 * Showcases AE with the HITL + branching primitives stacked —
 * the canonical "bot does the work, human owns the decision" pattern. */
const INCIDENT_AE_REMEDIATION: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -240 },
      width: 580,
      height: 210,
      data: {
        text:
          "INCIDENT AUTO-REMEDIATION (RPA + HITL)\n" +
          "Every inbound alert is classified, then routed: auto-remediation gets a Human Approval BEFORE the runbook executes via AutomationEdge, investigations get a diagnostic plan, everything else pages on-call.\n" +
          "\n" +
          "For: SRE · platform-ops · incident commanders · compliance\n" +
          "\n" +
          "At ~20 incidents/day (mixed severity):\n" +
          "• MTTR on runbook-covered: 45 min → ~8 min (one human tap, then RPA)\n" +
          "• Unauthorised destructive actions: always 0 — HITL gate on record\n" +
          "• Post-mortem narrative: ~30 min manual write-up → ~30 s BALANCED-tier\n" +
          "\n" +
          "Needs: AutomationEdge · PagerDuty routing key · Slack incident channel · HITL approvers",
        color: "pink",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 260 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive an alert",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/incidents/alert" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 260 },
      data: {
        label: "Intent Classifier",
        displayName: "What action does this alert need?",
        nodeCategory: "nlp",
        config: {
          icon: "target",
          utteranceExpression: "trigger.alert_text",
          intents: [
            {
              name: "auto_remediate",
              description:
                "Known failure with a documented runbook — safe to attempt automated remediation.",
              examples: [
                "service X is failing its healthcheck and the runbook says restart",
                "queue Y is backed up > 1000 — drain job",
                "certificate expired on host Z — rotate",
              ],
              priority: 100,
            },
            {
              name: "investigate",
              description:
                "Symptoms known but cause not obvious — gather diagnostics before acting.",
              examples: [
                "latency spike across several services",
                "intermittent 502s from the frontend",
                "unexplained memory growth",
              ],
              priority: 100,
            },
            {
              name: "manual_handling",
              description:
                "High-risk or out-of-scope for automation — assign to oncall.",
              examples: [
                "suspected security incident",
                "customer-facing data inconsistency",
                "unknown alert source",
              ],
              priority: 50,
            },
          ],
          mode: "hybrid",
          ...TEMPLATE_TIER_FAST,
          embeddingProvider: "openai",
          embeddingModel: "text-embedding-3-small",
          confidenceThreshold: 0.6,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 260 },
      data: {
        label: "Switch",
        displayName: "Route to the right response",
        nodeCategory: "logic",
        config: {
          icon: "git-fork",
          expression: "node_2.intents[0]",
          cases: [
            { value: "auto_remediate", label: "Auto-remediate (HITL + AE)" },
            { value: "investigate", label: "Investigate" },
            { value: "manual_handling", label: "Page oncall" },
          ],
          defaultLabel: "Unknown → oncall",
          matchMode: "equals",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 860, y: 80 },
      data: {
        label: "Human Approval",
        displayName: "Approve before remediation runs",
        nodeCategory: "action",
        config: {
          icon: "user-check",
          approvalMessage:
            "Auto-remediation detected: `{{ trigger.runbook_id }}` — {{ trigger.alert_text }}.\n" +
            "Approve to run AE workflow `{{ trigger.runbook_id }}` against `{{ trigger.target }}`. " +
            "Your approver identity is captured for the audit trail.",
          timeout: 1800,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1160, y: 80 },
      data: {
        label: "AutomationEdge",
        displayName: "Execute the runbook (RPA)",
        nodeCategory: "action",
        config: {
          icon: "bot",
          integrationLabel: "",
          // The workflow name comes from trigger metadata so different
          // runbooks reuse the same template. In prod, validate
          // trigger.runbook_id against an allowlist before reaching
          // this node (e.g. via a Code node upstream).
          workflowName: "{{ trigger.runbook_id }}",
          authMode: "ae_session",
          credentialsSecretPrefix: "AUTOMATIONEDGE",
          inputMapping: [
            {
              name: "target",
              type: "string",
              valueExpression: "trigger.target",
            },
            {
              name: "alert_id",
              type: "string",
              valueExpression: "trigger.alert_id",
            },
            {
              name: "approved_by",
              type: "string",
              // Approval payload carries the approver identity captured
              // by HITL-01 (approval_audit_log).
              valueExpression: "node_4.approver",
            },
          ],
          completionMode: "webhook",
          webhookAuth: "hmac",
          webhookCallbackBaseUrl: "{{ env.ORCHESTRATOR_PUBLIC_BASE_URL }}",
          pollIntervalSeconds: 15,
          timeoutSeconds: 1800,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1460, y: 80 },
      data: {
        label: "LLM Agent",
        displayName: "Summarise what happened",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "Write a concise incident-channel update (2-4 lines). " +
            "Inputs: trigger.alert_text (the original alert), node_4.approver (who approved), " +
            "node_5.result (AE terminal status + output payload). " +
            "Mention the runbook id, whether it succeeded, and the one next step if it failed.",
          temperature: 0.3,
          maxTokens: 512,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 1760, y: 80 },
      data: {
        label: "Notification",
        displayName: "Post the outcome to the incident channel",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_INCIDENT_WEBHOOK }}",
          username: "Remediation Bot",
          iconEmoji: ":construction:",
          messageTemplate: "{{ node_6.response }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_8",
      type: "agenticNode",
      position: { x: 860, y: 260 },
      data: {
        label: "LLM Agent",
        displayName: "Draft a diagnostic plan for on-call",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You are the triage analyst. The alert is: {{ trigger.alert_text }}. " +
            "Outline a short, ordered checklist (3-5 steps) for the oncall to gather diagnostics. " +
            "Cite likely log sources and dashboards by name when reasonable.",
          temperature: 0.3,
          maxTokens: 768,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 1160, y: 260 },
      data: {
        label: "Notification",
        displayName: "Send diagnostic plan to Slack",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_INCIDENT_WEBHOOK }}",
          username: "Triage Bot",
          iconEmoji: ":mag:",
          messageTemplate:
            ":mag: *Investigate* — {{ trigger.alert_text }}\n\n{{ node_8.response }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_10",
      type: "agenticNode",
      position: { x: 860, y: 440 },
      data: {
        label: "Notification",
        displayName: "Page on-call (PagerDuty)",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "pagerduty",
          destination: "{{ env.PAGERDUTY_ROUTING_KEY }}",
          severity: "warning",
          eventAction: "trigger",
          messageTemplate:
            "[manual-handling] {{ trigger.alert_text }} · target={{ trigger.target }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    {
      id: "e_3_4",
      source: "node_3",
      target: "node_4",
      sourceHandle: "auto_remediate",
      label: "Auto-remediate",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_8",
      source: "node_3",
      target: "node_8",
      sourceHandle: "investigate",
      label: "Investigate",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_10",
      source: "node_3",
      target: "node_10",
      sourceHandle: "manual_handling",
      label: "Manual",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_10_default",
      source: "node_3",
      target: "node_10",
      sourceHandle: "default",
      label: "Unknown → oncall",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "4 3" },
      animated: true,
    },
    { id: "e_4_5", source: "node_4", target: "node_5" },
    { id: "e_5_6", source: "node_5", target: "node_6" },
    { id: "e_6_7", source: "node_6", target: "node_7" },
    { id: "e_8_9", source: "node_8", target: "node_9" },
  ],
};


// ---------------------------------------------------------------------------
// TMPL-03 — Cycle graph example.
//
// A genuinely cyclic workflow: Drafter → Reflection → Condition →
// (loop back to Drafter if the Reflection says needs_revision,
//  else Finalizer → Notification). The loopback edge carries the
// cycle, gated by the Condition's branch so it only fires on the
// "revise" path. Hard cap of 3 iterations via the loopback's
// maxIterations; combined with the Reflection's quality gate this
// gives a deterministic "draft until good enough OR ship what we have"
// pattern that's valuable for legal/policy notes, customer replies,
// proposal drafts, and anywhere a single-shot LLM answer is too risky.
//
// Loop invariants:
//   * On iteration N > 0 the Drafter sees the prior Reflection's
//     feedback in context (node_3.feedback) and is prompted to
//     incorporate it.
//   * The Reflection returns a JSON object with
//     ``needs_revision`` (bool) and ``feedback`` (string) — see
//     outputKeys + reflectionPrompt below.
//   * The Condition branches on ``node_3.needs_revision == true``.
//     True fires ONLY the loopback (back to Drafter); false fires
//     the forward edge to the Finalizer.
// ---------------------------------------------------------------------------
const CONTENT_REFINEMENT_LOOP: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -220 },
      width: 560,
      height: 196,
      data: {
        text:
          "SELF-REVIEWING DRAFTS — AI CRITIC REFINES UNTIL ACCEPTED\n" +
          "Drafter writes, a strict Critic scores & feeds back, and the cycle repeats (max 3 passes) until the draft passes the quality bar — or the cap is hit. Both run on BALANCED tier so the gate actually catches mistakes.\n" +
          "\n" +
          "For: legal · policy · proposals · customer-facing comms · product marketing\n" +
          "\n" +
          "At ~80 drafts/day:\n" +
          "• Drafting + review time: 35 min → ~90 s end-to-end\n" +
          "• Revision loops until accepted: typically 1–2 (3 is the hard cap)\n" +
          "• Accepted-first-try rate: ~60 % with the critic tuned strict\n" +
          "\n" +
          "Needs: Slack review channel · pair with Knowledge Retrieval if you want grounded answers",
        color: "yellow",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a drafting request",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/drafts/refine" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 220 },
      data: {
        label: "LLM Agent",
        displayName: "Write the draft (or revise it)",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "You draft business-grade written content. The request is: " +
            "topic={{ trigger.topic }}, audience={{ trigger.audience }}, tone={{ trigger.tone | default('neutral') }}. " +
            "If this is a revision (node_3 exists in context), INCORPORATE the critic's feedback verbatim: " +
            "{{ node_3.feedback | default('') }}. Otherwise produce the first draft. " +
            "Respond with ONLY the draft text — no preamble, no post-script.",
          temperature: 0.5,
          maxTokens: 2048,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 220 },
      data: {
        label: "Reflection",
        displayName: "Score the draft & suggest edits",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          // Reflection runs on BALANCED (2.5-pro) because the gate
          // quality drives the whole loop — a lenient critic sends
          // mediocre copy, a hallucinating critic traps the loop.
          ...TEMPLATE_TIER_BALANCED,
          reflectionPrompt:
            "You are a strict content critic. Review the latest draft (node_2.response) against the brief " +
            "(trigger.topic, trigger.audience, trigger.tone). Score on correctness, clarity, tone-fit, and " +
            "completeness. Return STRICT JSON: " +
            '{"needs_revision": <bool>, "score": <0.0-1.0>, "feedback": "<what to change in the next pass; ' +
            'specific and actionable. If no changes needed, leave empty.>"}. ' +
            "Do not rewrite the draft yourself — just critique it.",
          outputKeys: ["needs_revision", "score", "feedback"],
          maxHistoryNodes: 5,
          temperature: 0.2,
          maxTokens: 1024,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 860, y: 220 },
      data: {
        label: "Condition",
        displayName: "Does the critic want a rewrite?",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: "node_3.needs_revision == true",
          trueLabel: "Revise ↻",
          falseLabel: "Accept",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1160, y: 220 },
      data: {
        label: "LLM Agent",
        displayName: "Polish for publication",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "The drafter + critic are done. Take node_2.response as the approved copy. " +
            "Format it for publication: add a 1-line subject/title if appropriate, preserve paragraphing, " +
            "and output the final text only. Don't editorialise or add disclaimers.",
          temperature: 0.2,
          maxTokens: 2048,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1460, y: 220 },
      data: {
        label: "Notification",
        displayName: "Post the accepted draft to the review channel",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_DRAFTS_WEBHOOK }}",
          username: "Drafts Bot",
          iconEmoji: ":pencil2:",
          messageTemplate:
            ":pencil2: *Draft accepted* (score {{ node_3.score | round(2) }}, revision pass)\n" +
            "Topic: {{ trigger.topic }} · Audience: {{ trigger.audience }}\n\n" +
            "{{ node_5.response }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    { id: "e_3_4", source: "node_3", target: "node_4" },
    // Accept branch: forward to the finaliser → notification.
    {
      id: "e_4_5",
      source: "node_4",
      target: "node_5",
      sourceHandle: "false",
      label: "Accept",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    { id: "e_5_6", source: "node_5", target: "node_6" },
    // CYCLIC-01 loopback: on the "true" (needs revision) branch, fire
    // a loopback back to the Drafter. The dag_runner gates loopbacks
    // on the Condition's chosen branch via ``sourceHandle``, so this
    // only fires when needs_revision==true. Hard cap of 3 bounds a
    // stubborn critic / flaky LLM.
    {
      id: "e_4_2_loopback",
      source: "node_4",
      target: "node_2",
      sourceHandle: "true",
      type: "loopback",
      data: { maxIterations: 3 },
      label: "↻ revise with feedback",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "6 4" },
      animated: true,
    },
  ],
};


// ---------------------------------------------------------------------------
// TMPL-04 — A2A (Agent-to-Agent) example workflows.
//
// The A2A Agent Call node delegates a task to an external A2A-compatible
// agent identified by its /.well-known/agent.json discovery document.
// The orchestrator acts as the conductor: it picks which external agent
// to call, composes the input message, and synthesises the final answer
// from the remote responses. Great when the specialist knowledge lives
// outside the orchestrator (a compliance team's agent, a partner's
// research agent, an in-house RAG stack with its own A2A interface).
//
// See ``codewiki/api-reference.md`` §A2A for the full JSON-RPC surface.
// ---------------------------------------------------------------------------

/** A2A research swarm — sequential delegation:
 *   webhook → external researcher A2A agent → external fact-checker A2A
 *   agent (reads researcher output) → synthesiser LLM → Slack.
 *
 * Showcases chaining two different external agents. Each agent card URL
 * is distinct; each call has its own ``apiKeySecret`` vault reference.
 * The second A2A call sends the FIRST agent's output as its input, so
 * authors can see the ``messageExpression`` composition pattern. */
const A2A_RESEARCH_SWARM: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -180 },
      width: 540,
      height: 160,
      data: {
        text:
          "WHEN TO USE THIS PATTERN — CHAIN TWO A2A AGENTS\n" +
          "One external agent's output becomes the next one's input via a composed messageExpression. Great when specialist agents must run sequentially (research then fact-check; summarise then translate).\n" +
          "\n" +
          "Production reasons to reach for this shape:\n" +
          "• Independent vendors / partner agents with their own agent-card URLs\n" +
          "• Separate API keys / rate limits / audit trails per call (each node is its own row)\n" +
          "• Orchestrator composes the input string from trigger + prior agent's response",
        color: "purple",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 220 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive a research question",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/research/ask" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 220 },
      data: {
        label: "A2A Agent Call",
        displayName: "Web researcher (A2A)",
        nodeCategory: "action",
        config: {
          icon: "network",
          agentCardUrl: "{{ env.A2A_RESEARCHER_AGENT_CARD_URL }}",
          // Blank skillId = use the remote agent's first advertised
          // skill. Set it to an explicit id (e.g. "deep_research")
          // once you know which skill the remote agent exposes.
          skillId: "",
          messageExpression: "trigger.question",
          apiKeySecret: "{{ env.A2A_RESEARCHER_API_KEY }}",
          timeoutSeconds: 300,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 220 },
      data: {
        label: "A2A Agent Call",
        displayName: "Fact checker (A2A)",
        nodeCategory: "action",
        config: {
          icon: "network",
          agentCardUrl: "{{ env.A2A_FACTCHECKER_AGENT_CARD_URL }}",
          skillId: "",
          // Chain: the fact-checker reads the researcher's response as
          // its input. A2A responses land on node_2.response (plain
          // text) or node_2.artifacts (rich outputs — FilePart /
          // DataPart per A2A-01.c). Here we pass both the original
          // question and the researcher's draft so the checker has
          // full context.
          messageExpression:
            "'Claim to verify: ' + node_2.response + '\\n\\nOriginal question: ' + trigger.question",
          apiKeySecret: "{{ env.A2A_FACTCHECKER_API_KEY }}",
          timeoutSeconds: 300,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 860, y: 220 },
      data: {
        label: "LLM Agent",
        displayName: "Synthesise final answer",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          // BALANCED — this is reasoning-heavy (resolve disagreements
          // between two independent external agents). A local FAST
          // tier often misses subtle contradictions the fact-checker
          // flagged.
          ...TEMPLATE_TIER_BALANCED,
          systemPrompt:
            "You are the research lead. You have two remote-agent responses: " +
            "Researcher draft: `{{ node_2.response }}`\n" +
            "Fact-checker findings: `{{ node_3.response }}`\n\n" +
            "Write a final answer to the user's question (trigger.question). " +
            "Cite any facts the checker confirmed; call out any claims it disputed " +
            "and correct them. Keep it under 400 words unless the question warrants more.",
          temperature: 0.3,
          maxTokens: 2048,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 1160, y: 220 },
      data: {
        label: "Notification",
        displayName: "Post answer to research channel",
        nodeCategory: "notification",
        config: {
          icon: "bell",
          channel: "slack_webhook",
          destination: "{{ env.SLACK_RESEARCH_WEBHOOK }}",
          username: "Research Bot",
          iconEmoji: ":microscope:",
          messageTemplate:
            ":microscope: *Q:* {{ trigger.question }}\n\n{{ node_4.response }}",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    { id: "e_3_4", source: "node_3", target: "node_4" },
    { id: "e_4_5", source: "node_4", target: "node_5" },
  ],
};

/** Specialist A2A routing — Intent Classifier + Switch fans out to
 *  per-domain external agents and converges on a single notification.
 *
 *   webhook → Intent Classifier (finance / legal / technical / general) →
 *   Switch → {A2A finance agent | A2A legal agent | A2A technical agent
 *             | fallback inline LLM} → Notification.
 *
 * This is the production pattern when specialist knowledge lives with
 * partner / sister teams exposing their own A2A agents — each
 * ``agentCardUrl`` points at a different remote deployment. The
 * "general" fallback stays inline so the workflow still answers even
 * if every A2A partner is unreachable. */
const A2A_SPECIALIST_ROUTING: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "sticky_overview",
      type: "stickyNote",
      position: { x: 0, y: -240 },
      width: 580,
      height: 210,
      data: {
        text:
          "ROUTE TO THE RIGHT EXPERT TEAM\n" +
          "Intent Classifier understands the question in <2s, then a Switch fans it to external finance / legal / technical A2A specialists. A local general-purpose LLM catches everything else so no caller hits a dead end, even if every partner A2A is offline.\n" +
          "\n" +
          "For: internal help-desks · customer support · partner-federated agent networks\n" +
          "\n" +
          "At ~400 questions/day:\n" +
          "• First-response time: ~2 min triage + email → ~3 s end-to-end\n" +
          "• Mis-routes: ~15 % (manual triage) → ~5 % (hybrid Intent Classifier)\n" +
          "• Coverage: 100 % — local fallback ensures every question gets answered\n" +
          "\n" +
          "Needs: A2A agent cards + API keys per specialist · optional OpenAI/Vertex key for local fallback",
        color: "purple",
      },
    },
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 320 },
      data: {
        label: "Webhook Trigger",
        displayName: "Receive the user's question",
        nodeCategory: "trigger",
        config: { icon: "webhook", method: "POST", path: "/ask/specialist" },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 260, y: 320 },
      data: {
        label: "Intent Classifier",
        displayName: "Which expert team should handle this?",
        nodeCategory: "nlp",
        config: {
          icon: "target",
          utteranceExpression: "trigger.message",
          intents: [
            {
              name: "finance",
              description:
                "Questions about billing, pricing, budgets, contracts, SOX/PCI compliance dollar figures.",
              examples: [
                "what is my monthly spend",
                "when is my contract renewal",
                "can we capitalise this expense",
                "invoice dispute",
              ],
              priority: 100,
            },
            {
              name: "legal",
              description:
                "Questions about NDAs, DPAs, MSAs, regulatory exposure, IP, employment law.",
              examples: [
                "is our NDA template GDPR-compliant",
                "can we terminate this contract",
                "who owns the IP on this repo",
                "legal review of these terms",
              ],
              priority: 100,
            },
            {
              name: "technical",
              description:
                "Deep technical questions about the product, architecture, APIs, integrations.",
              examples: [
                "how does the A2A protocol handle authentication",
                "what's the retention for execution logs",
                "how do I integrate webhook signing",
                "debug a Vertex AI 403 error",
              ],
              priority: 100,
            },
            {
              name: "general",
              description:
                "Casual, out-of-scope, or cross-cutting questions — answered locally.",
              examples: [
                "hi",
                "what can you do",
                "I'm not sure who to ask",
              ],
              priority: 50,
            },
          ],
          mode: "hybrid",
          ...TEMPLATE_TIER_FAST,
          embeddingProvider: "openai",
          embeddingModel: "text-embedding-3-small",
          confidenceThreshold: 0.6,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 560, y: 320 },
      data: {
        label: "Switch",
        displayName: "Hand off to the matching team",
        nodeCategory: "logic",
        config: {
          icon: "git-fork",
          expression: "node_2.intents[0]",
          cases: [
            { value: "finance", label: "Finance A2A" },
            { value: "legal", label: "Legal A2A" },
            { value: "technical", label: "Technical A2A" },
            { value: "general", label: "General (local)" },
          ],
          defaultLabel: "Unknown → general",
          matchMode: "equals",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 860, y: 60 },
      data: {
        label: "A2A Agent Call",
        displayName: "Ask the Finance specialist agent",
        nodeCategory: "action",
        config: {
          icon: "network",
          agentCardUrl: "{{ env.A2A_FINANCE_AGENT_CARD_URL }}",
          skillId: "",
          messageExpression: "trigger.message",
          apiKeySecret: "{{ env.A2A_FINANCE_API_KEY }}",
          timeoutSeconds: 300,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 860, y: 220 },
      data: {
        label: "A2A Agent Call",
        displayName: "Ask the Legal specialist agent",
        nodeCategory: "action",
        config: {
          icon: "network",
          agentCardUrl: "{{ env.A2A_LEGAL_AGENT_CARD_URL }}",
          skillId: "",
          messageExpression: "trigger.message",
          apiKeySecret: "{{ env.A2A_LEGAL_API_KEY }}",
          timeoutSeconds: 300,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 860, y: 380 },
      data: {
        label: "A2A Agent Call",
        displayName: "Ask the Technical specialist agent",
        nodeCategory: "action",
        config: {
          icon: "network",
          agentCardUrl: "{{ env.A2A_TECHNICAL_AGENT_CARD_URL }}",
          skillId: "",
          messageExpression: "trigger.message",
          apiKeySecret: "{{ env.A2A_TECHNICAL_API_KEY }}",
          timeoutSeconds: 300,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 860, y: 540 },
      data: {
        label: "LLM Agent",
        displayName: "Answer locally (general fallback)",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          ...TEMPLATE_TIER_FAST,
          systemPrompt:
            "You handle general / out-of-scope questions locally. " +
            "Answer briefly and point the user at the specialist channels " +
            "(finance, legal, technical) when relevant. Stay on-brand.",
          temperature: 0.45,
          maxTokens: 512,
          memoryEnabled: false,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_8",
      type: "agenticNode",
      position: { x: 1180, y: 320 },
      data: {
        label: "Merge",
        displayName: "Collect whichever team answered",
        nodeCategory: "logic",
        config: {
          icon: "git-merge",
          // waitAny: only one specialist path fires per invocation
          // (the Switch prunes the others), so the Merge must NOT
          // wait for every predecessor — it unblocks the moment the
          // chosen specialist returns.
          strategy: "waitAny",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 1460, y: 320 },
      data: {
        label: "Bridge User Reply",
        displayName: "Send the answer back to the user",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          // Pick whichever upstream ran. Each specialist path writes
          // its output to its own node id; the reply expression walks
          // them in priority order and picks the first non-empty one.
          messageExpression:
            "(node_4.response or node_5.response or node_6.response or node_7.response)",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    {
      id: "e_3_4",
      source: "node_3",
      target: "node_4",
      sourceHandle: "finance",
      label: "Finance",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_5",
      source: "node_3",
      target: "node_5",
      sourceHandle: "legal",
      label: "Legal",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_6",
      source: "node_3",
      target: "node_6",
      sourceHandle: "technical",
      label: "Technical",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_7",
      source: "node_3",
      target: "node_7",
      sourceHandle: "general",
      label: "General",
      style: { stroke: "#14b8a6", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_3_7_default",
      source: "node_3",
      target: "node_7",
      sourceHandle: "default",
      label: "Unknown → general",
      style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "4 3" },
      animated: true,
    },
    { id: "e_4_8", source: "node_4", target: "node_8" },
    { id: "e_5_8", source: "node_5", target: "node_8" },
    { id: "e_6_8", source: "node_6", target: "node_8" },
    { id: "e_7_8", source: "node_7", target: "node_8" },
    { id: "e_8_9", source: "node_8", target: "node_9" },
  ],
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
    name: "Helpdesk auto-triage — route every ticket to the right team",
    description:
      "Intent Classifier understands each inbound ticket in <2s and a Switch hands it to the right specialist (orders / technical / general). A parallel SLA checklist drafts internal notes in ForEach; the technical reply gets an L2 Human Approval gate before going back to the customer. Bridge User Reply makes this sync-chat-compatible. At ~1,000 tickets/day this saves roughly 330 agent-hours per day versus a manual triage queue.",
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
    name: "Governed document review — legal gate before external send",
    description:
      "A BALANCED-tier LLM (Gemini 2.5 Pro) summarises submissions and flags legal/policy risks. A named approver signs off via the pending-approvals badge with a full compliance audit trail (approver · reason · patch). Nothing leaves the building without a human on record. At ~50 docs/day this trims initial review from ~25 min to ~2 min and approval turnaround from ~2 h to ~15 min.",
    category: "operations",
    tags: ["compliance", "approval", "documents", "audit"],
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
    name: "Ops engineer assistant — diagnostics · remediation · RCA",
    description:
      "Every inbound alert is classified in <2s and handed to the right specialist path: Diagnostics runs tool-backed investigation (ReAct + MCP), Remediation takes corrective action behind a Human Approval gate, RCA drafts an exec-ready postmortem on BALANCED tier, and Default Ops fields everything else. Bridge User Reply keeps it sync-chat friendly. At ~200 alerts/day this typically cuts MTTR on runbook-covered failures from ~45 min to ~8 min and collapses RCA drafting from hours to ~90s.",
    category: "operations",
    tags: ["router", "ReAct", "AIOps", "HITL"],
    graph: EXAMPLE_OPERATIONS_ROUTING_WORKFLOW,
  }),
  asTemplate({
    id: "316da27c-7fc0-4535-b246-757dac1aafc7",
    name: "AE_Ops_Routing_Template",
    description:
      "AE_Ops_Routing_Template: Modern AIOps routing with specialist specialists for diagnostics, remediation, and RCA. Features a ReAct-powered Ops Orchestrator for real-time AutomationEdge status checks and guidance. Gates destructive remediation behind human approval.",
    category: "operations",
    tags: ["AE_Ops_Routing_Template", "automationedge", "AIOps", "ReAct", "HITL", "router"],
    graph: AE_OPS_ROUTING_TEMPLATE,
  }),
  asTemplate({
    id: "rag-knowledge-qa",
    name: "RAG knowledge base Q&A",
    description:
      "Search a knowledge base for relevant chunks, then answer the user's question with grounded context and multi-turn memory. For mixed-media KBs (screenshots, PDFs, audio transcripts), pick gemini-embedding-2 at KB creation time — a single 3072-dim multimodal vector space covers text + image + video + audio.",
    category: "research",
    tags: ["RAG", "knowledge", "retrieval", "grounded", "multimodal"],
    graph: RAG_KNOWLEDGE_QA,
  }),
  asTemplate({
    id: "scheduled-notification",
    name: "Scheduled report + Slack alert",
    description:
      "Cron-triggered LLM summary sent to a Slack channel via Notification node. Swap the destination for Teams, Discord, or email.",
    category: "notification",
    tags: ["schedule", "cron", "slack", "notification", "report"],
    graph: SCHEDULED_NOTIFICATION,
  }),
  asTemplate({
    id: "nlp-intent-entity",
    name: "NLP intent + entity routing",
    description:
      "Hybrid Intent Classifier + Entity Extractor determine user intent and slot values, then branch to a specialist agent per intent.",
    category: "nlp",
    tags: ["intent", "NLP", "entity", "classifier", "slots"],
    graph: NLP_INTENT_ENTITY,
  }),
  asTemplate({
    id: "episode-archive-support",
    name: "Support chatbot with episode archiving",
    description:
      "Multi-turn support assistant that detects issue resolution and archives the active episode into episodic memory on close.",
    category: "customer-support",
    tags: ["archive", "episode", "memory", "chatbot", "HITL"],
    graph: EPISODE_ARCHIVE_SUPPORT,
  }),
  // NODES-01 showcase templates — logic-primitive demos.
  asTemplate({
    id: "priority-router-switch",
    name: "Priority router (Switch)",
    description:
      "Route incidents by priority (P1/P2/P3/P4) to PagerDuty, Slack, or email in one Switch node — no classifier required when the payload already carries the tier. Showcases NODES-01.a with matchMode=equals_ci so 'p1' and 'P1' both match.",
    category: "notification",
    tags: ["switch", "routing", "multi-branch", "priority", "notification"],
    graph: PRIORITY_ROUTING_SWITCH,
  }),
  asTemplate({
    id: "retry-until-success-while",
    name: "Retry until success (While)",
    description:
      "Hit a flaky upstream API until it returns 2xx — or give up after 5 attempts and report the final status to Slack. Showcases NODES-01.b: the While node re-evaluates its condition before each iteration and _loop_index is available inside the expression.",
    category: "operations",
    tags: ["while", "loop", "retry", "http", "backoff"],
    graph: RETRY_UNTIL_SUCCESS_WHILE,
  }),
  asTemplate({
    id: "agent-tool-loopback",
    name: "Agent ↔ tool loopback",
    description:
      "Planner LLM picks a tool, MCP Tool executes it, and the result loops back to the planner for the next step. Capped at 5 loops. Showcases CYCLIC-01 loopback edges — the explicit node-level version of what ReAct Agent does internally, giving you per-step pins, observability, and custom gating.",
    category: "research",
    tags: ["loopback", "cyclic", "agent", "tools", "MCP", "ReAct"],
    graph: AGENT_TOOL_LOOPBACK,
  }),
  // TMPL-02 — AutomationEdge (RPA) examples.
  asTemplate({
    id: "invoice-ae-erp",
    name: "Invoice auto-post to ERP (RPA) — with exception handling",
    description:
      "Incoming invoices are parsed by an Entity Extractor (invoice number, amount, vendor, due date) and posted to the ERP via AutomationEdge. A post-AE Condition branches on the RPA result so successes confirm to AP on Slack and failures escalate to finance ops by email with the AE request id. At ~200 invoices/day this typically cuts keying time from ~4 min/invoice to ~20 s and halves posting errors.",
    category: "operations",
    tags: ["automationedge", "RPA", "ERP", "invoice", "entity-extraction", "AP"],
    graph: INVOICE_AE_ERP,
  }),
  asTemplate({
    id: "incident-ae-remediation",
    name: "Incident auto-remediation — runbook with human approval",
    description:
      "Every alert is classified then routed three ways. Auto-remediation gates Human Approval BEFORE the runbook executes via AutomationEdge (destructive actions never bypass a human on record); the BALANCED-tier narrator then posts what happened to the incident channel. Investigations draft a diagnostic checklist; everything else pages on-call. Cuts MTTR on runbook-covered failures from ~45 min to ~8 min with zero unauthorised destructive actions.",
    category: "operations",
    tags: ["automationedge", "RPA", "incident", "HITL", "switch", "approval", "remediation"],
    graph: INCIDENT_AE_REMEDIATION,
  }),
  // TMPL-03 — cycle-graph use case: iterative draft refinement.
  asTemplate({
    id: "content-refinement-loop",
    name: "Self-reviewing drafts — AI critic refines until accepted",
    description:
      "Draft → Critic → (loop back if needs work) → Finalize → Slack. Drafter writes business-grade copy; a strict Reflection critic scores it on correctness, clarity, and tone-fit and sends specific feedback; a CYCLIC-01 loopback runs up to 3 revision passes before the cycle exits. Both Drafter and Critic run on BALANCED tier so the quality gate is meaningful. Trims a 35-minute draft-and-review cycle to ~90 seconds end-to-end for legal notes, proposals, and customer-facing replies.",
    category: "research",
    tags: ["cyclic", "loopback", "reflection", "drafting", "policy", "proposal", "quality-gate"],
    graph: CONTENT_REFINEMENT_LOOP,
  }),
  // TMPL-04 — Agent-to-Agent (A2A) delegation.
  asTemplate({
    id: "a2a-research-swarm",
    name: "A2A research swarm (researcher → fact-checker)",
    description:
      "Chain two external A2A agents by agent-card URL: a web researcher drafts an answer, then a fact-checker verifies the claims by reading the researcher's output. A BALANCED synthesiser LLM resolves disagreements and posts the final answer to Slack. Showcases the A2A Agent Call node with distinct agentCardUrl + apiKeySecret per call, and composing one agent's response into the next agent's messageExpression.",
    category: "research",
    tags: ["A2A", "agent-to-agent", "delegation", "research", "fact-check", "chain"],
    graph: A2A_RESEARCH_SWARM,
  }),
  asTemplate({
    id: "a2a-specialist-routing",
    name: "Route to the right expert — finance / legal / technical",
    description:
      "Intent Classifier understands each incoming question in <2s, then a Switch hands it to the matching external A2A specialist (finance, legal, or technical) by agent-card URL. A local general-purpose LLM catches everything else so no caller hits a dead end even if every partner A2A is offline. Merge(waitAny) converges on a single Bridge User Reply. At ~400 questions/day this replaces manual triage + email routing (~2 min per question) with ~3s end-to-end.",
    category: "operations",
    tags: ["A2A", "agent-to-agent", "routing", "switch", "specialist", "delegation"],
    graph: A2A_SPECIALIST_ROUTING,
  }),
];

export const TEMPLATE_CATEGORIES: { id: TemplateCategory; label: string }[] = [
  { id: "getting-started", label: "Getting started" },
  { id: "customer-support", label: "Customer support" },
  { id: "operations", label: "Operations" },
  { id: "research", label: "Research" },
  { id: "notification", label: "Notifications" },
  { id: "nlp", label: "NLP" },
];

export function getWorkflowTemplate(id: string): WorkflowTemplate | undefined {
  return WORKFLOW_TEMPLATES.find((t) => t.id === id);
}
