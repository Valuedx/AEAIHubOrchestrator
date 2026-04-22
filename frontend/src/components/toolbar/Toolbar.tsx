import { useEffect, useState } from "react";
import {
  Save,
  Play,
  FolderOpen,
  FilePlus,
  History,
  Loader2,
  CircleDot,
  CircleCheck,
  CircleX,
  Pause,
  PauseCircle,
  Ban,
  LayoutTemplate,
  Undo2,
  Redo2,
  Activity,
  Bot,
  Database,
  KeyRound,
  StickyNote as StickyNoteIcon,
  Keyboard,
  Power,
  PowerOff,
  Globe,
  FlaskConical,
  Cloud,
  SlidersHorizontal,
  Key,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useWorkflowStore } from "@/store/workflowStore";
import { useFlowStore } from "@/store/flowStore";
import { WorkflowListDialog } from "@/components/toolbar/WorkflowListDialog";
import { VersionHistoryDialog } from "@/components/toolbar/VersionHistoryDialog";
import { InstanceHistoryDialog } from "@/components/toolbar/InstanceHistoryDialog";
import { ValidationDialog } from "@/components/toolbar/ValidationDialog";
import { TemplateGalleryDialog } from "@/components/toolbar/TemplateGalleryDialog";
import { KnowledgeBaseDialog } from "@/components/toolbar/KnowledgeBaseDialog";
import { SecretsDialog } from "@/components/toolbar/SecretsDialog";
import { IntegrationsDialog } from "@/components/toolbar/IntegrationsDialog";
import { McpServersDialog } from "@/components/toolbar/McpServersDialog";
import { VertexProjectsDialog } from "@/components/toolbar/VertexProjectsDialog";
import { TenantPolicyDialog } from "@/components/toolbar/TenantPolicyDialog";
import { LlmCredentialsDialog } from "@/components/toolbar/LlmCredentialsDialog";
import { HotkeyCheatsheet } from "@/components/toolbar/HotkeyCheatsheet";
import { ApiPlaygroundDialog } from "@/components/toolbar/ApiPlaygroundDialog";
import { validateWorkflow, type ValidationError } from "@/lib/validateWorkflow";
import { isTextEditingTarget } from "@/lib/keyboardUtils";

const STATUS_CONFIG: Record<string, { icon: typeof CircleDot; label: string; className: string }> = {
  queued: { icon: CircleDot, label: "Queued", className: "text-muted-foreground" },
  running: { icon: Loader2, label: "Running", className: "text-blue-500 animate-spin" },
  completed: { icon: CircleCheck, label: "Completed", className: "text-green-500" },
  failed: { icon: CircleX, label: "Failed", className: "text-red-500" },
  suspended: { icon: Pause, label: "Suspended", className: "text-yellow-500" },
  paused: { icon: PauseCircle, label: "Paused", className: "text-cyan-600" },
  cancelled: { icon: Ban, label: "Cancelled", className: "text-orange-500" },
};

export function Toolbar() {
  const [listOpen, setListOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [instancesOpen, setInstancesOpen] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [validationErrors, setValidationErrors] = useState<ValidationError[]>([]);
  const [validationOpen, setValidationOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [kbOpen, setKbOpen] = useState(false);
  const [secretsOpen, setSecretsOpen] = useState(false);
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const [mcpServersOpen, setMcpServersOpen] = useState(false);
  const [vertexProjectsOpen, setVertexProjectsOpen] = useState(false);
  const [tenantPolicyOpen, setTenantPolicyOpen] = useState(false);
  const [llmCredentialsOpen, setLlmCredentialsOpen] = useState(false);
  const [cheatsheetOpen, setCheatsheetOpen] = useState(false);
  const [playgroundOpen, setPlaygroundOpen] = useState(false);

  // DV-06 — ? opens the hotkey cheatsheet from anywhere on the page.
  // Guard against input/textarea focus so typing "?" into a field
  // doesn't pop the dialog.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "?") return;
      if (isTextEditingTarget(e.target)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      e.preventDefault();
      setCheatsheetOpen(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const handleAddSticky = () => {
    // Dispatched here and consumed by FlowCanvas, which owns the
    // React Flow instance needed to translate viewport → flow coords.
    window.dispatchEvent(new CustomEvent("aeai:add-sticky"));
  };

  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const isDirty = useWorkflowStore((s) => s.isDirty);
  const loading = useWorkflowStore((s) => s.loading);
  const isExecuting = useWorkflowStore((s) => s.isExecuting);
  const activeInstance = useWorkflowStore((s) => s.activeInstance);
  const saveWorkflow = useWorkflowStore((s) => s.saveWorkflow);
  const executeWorkflow = useWorkflowStore((s) => s.executeWorkflow);
  const newWorkflow = useWorkflowStore((s) => s.newWorkflow);
  const setActive = useWorkflowStore((s) => s.setActive);
  const runSync = useWorkflowStore((s) => s.runSync);
  const setRunSync = useWorkflowStore((s) => s.setRunSync);
  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);
  const past = useFlowStore((s) => s.past);
  const future = useFlowStore((s) => s.future);
  const undo = useFlowStore((s) => s.undo);
  const redo = useFlowStore((s) => s.redo);

  const workflowName = currentWorkflow?.name || "Untitled Workflow";
  const status = activeInstance?.status;
  const StatusIcon = status ? STATUS_CONFIG[status]?.icon ?? CircleDot : null;

  const handleSave = () => {
    if (!currentWorkflow && !editingName) {
      setEditingName(true);
      setNameInput(workflowName);
      return;
    }
    saveWorkflow(editingName ? nameInput : undefined);
    setEditingName(false);
  };

  const handleNameSubmit = () => {
    saveWorkflow(nameInput || "Untitled Workflow");
    setEditingName(false);
  };

  const handleRun = () => {
    const errors = validateWorkflow(nodes, edges);
    if (errors.length > 0) {
      setValidationErrors(errors);
      setValidationOpen(true);
      return;
    }
    executeWorkflow();
  };

  const handleRunAnyway = () => {
    setValidationOpen(false);
    executeWorkflow();
  };

  return (
    <>
      <div className="flex items-center h-12 px-3 border-b bg-sidebar gap-2 shrink-0 min-w-0 overflow-x-auto">
        <div className="flex items-center gap-1.5 mr-2">
          <div className="h-6 w-6 rounded bg-primary flex items-center justify-center">
            <span className="text-xs font-bold text-primary-foreground">AE</span>
          </div>
          <span className="text-sm font-semibold hidden sm:inline">AI Hub</span>
        </div>

        <Separator orientation="vertical" className="h-6" />

        {editingName ? (
          <form
            onSubmit={(e) => { e.preventDefault(); handleNameSubmit(); }}
            className="flex items-center gap-1"
          >
            <Input
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              className="h-7 w-48 text-sm"
              autoFocus
              onBlur={handleNameSubmit}
            />
          </form>
        ) : (
          <button
            className="text-sm font-medium truncate max-w-[200px] hover:underline cursor-pointer"
            onClick={() => {
              setEditingName(true);
              setNameInput(currentWorkflow?.name || "Untitled Workflow");
            }}
          >
            {workflowName}
          </button>
        )}

        {isDirty && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
            unsaved
          </Badge>
        )}

        {currentWorkflow && (
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            v{currentWorkflow.version}
          </Badge>
        )}

        {currentWorkflow && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setActive(!currentWorkflow.is_active)}
            title={
              currentWorkflow.is_active
                ? "Active — Schedule Triggers will fire. Click to pause."
                : "Inactive — Schedule Triggers are paused. Manual Run still works. Click to resume."
            }
            className={
              currentWorkflow.is_active
                ? "gap-1.5 text-green-600 hover:text-green-600"
                : "gap-1.5 text-muted-foreground hover:text-foreground"
            }
          >
            {currentWorkflow.is_active ? (
              <Power className="h-3.5 w-3.5" />
            ) : (
              <PowerOff className="h-3.5 w-3.5" />
            )}
            <span className="text-[11px] font-medium hidden lg:inline">
              {currentWorkflow.is_active ? "Active" : "Inactive"}
            </span>
          </Button>
        )}

        <Separator orientation="vertical" className="h-6" />

        <Button
          variant="ghost"
          size="sm"
          onClick={() => { undo(); }}
          disabled={past.length === 0}
          title="Undo (Ctrl+Z)"
        >
          <Undo2 className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { redo(); }}
          disabled={future.length === 0}
          title="Redo (Ctrl+Y)"
        >
          <Redo2 className="h-4 w-4" />
        </Button>

        <div className="flex-1" />

        {status && StatusIcon && (
          <div className="flex items-center gap-1.5 mr-2">
            <StatusIcon className={`h-4 w-4 ${STATUS_CONFIG[status]?.className ?? ""}`} />
            <span className="text-xs text-muted-foreground">
              {STATUS_CONFIG[status]?.label ?? status}
            </span>
          </div>
        )}

        <Button variant="ghost" size="sm" onClick={() => newWorkflow()} title="New workflow">
          <FilePlus className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setTemplatesOpen(true)}
          title="Template gallery — starter DAGs, import/export JSON"
        >
          <LayoutTemplate className="h-4 w-4" />
        </Button>

        <Button variant="ghost" size="sm" onClick={() => setListOpen(true)} title="Open workflow">
          <FolderOpen className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setKbOpen(true)}
          title="Knowledge Bases — manage RAG knowledge bases and documents"
        >
          <Database className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setSecretsOpen(true)}
          title="Secrets — manage API keys and credentials for {{ env.* }}"
        >
          <KeyRound className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIntegrationsOpen(true)}
          title="Integrations — AutomationEdge connection defaults"
        >
          <Bot className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setMcpServersOpen(true)}
          title="MCP Servers — per-tenant registry of tool-providing servers"
        >
          <Globe className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setVertexProjectsOpen(true)}
          title="Vertex AI Projects — per-tenant GCP project routing for provider=vertex nodes"
        >
          <Cloud className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setLlmCredentialsOpen(true)}
          title="LLM Provider Credentials — per-tenant Google AI Studio / OpenAI / Anthropic API keys"
        >
          <Key className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setTenantPolicyOpen(true)}
          title="Tenant Policy — per-tenant overrides for execution quota, snapshot retention, and MCP pool size"
        >
          <SlidersHorizontal className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={handleAddSticky}
          title="Add sticky note (Shift+S)"
        >
          <StickyNoteIcon className="h-4 w-4" />
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setCheatsheetOpen(true)}
          title="Keyboard shortcuts (?)"
        >
          <Keyboard className="h-4 w-4" />
        </Button>

        {currentWorkflow && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setInstancesOpen(true)}
              title="Execution history (Runs)"
            >
              <Activity className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setHistoryOpen(true)}
              title="Version history (Drafts)"
            >
              <History className="h-4 w-4" />
            </Button>
          </>
        )}

        <Button
          variant="ghost"
          size="sm"
          onClick={handleSave}
          disabled={loading}
          title="Save workflow"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        </Button>

        <Separator orientation="vertical" className="h-6" />

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setPlaygroundOpen(true)}
          disabled={!currentWorkflow}
          title={
            !currentWorkflow
              ? "Save the workflow first — the Playground needs a stored workflow id"
              : "API Playground — test this workflow with an arbitrary payload"
          }
        >
          <FlaskConical className="h-4 w-4" />
        </Button>

        <label className="hidden md:flex items-center gap-2 mr-1 text-[11px] text-muted-foreground cursor-pointer whitespace-nowrap">
          <input
            type="checkbox"
            className="rounded border-input"
            checked={runSync}
            onChange={(e) => setRunSync(e.target.checked)}
          />
          Sync run
        </label>

        <Button
          variant="default"
          size="sm"
          onClick={handleRun}
          disabled={!currentWorkflow || isExecuting || loading}
          title={
            !currentWorkflow
              ? "Save the workflow first (toolbar Save) after templates or JSON import"
              : "Execute workflow"
          }
          className="gap-1.5"
        >
          {isExecuting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          <span className="hidden sm:inline">Run</span>
        </Button>
      </div>

      <KnowledgeBaseDialog open={kbOpen} onOpenChange={setKbOpen} />
      <SecretsDialog open={secretsOpen} onOpenChange={setSecretsOpen} />
      <IntegrationsDialog open={integrationsOpen} onOpenChange={setIntegrationsOpen} />
      <McpServersDialog open={mcpServersOpen} onOpenChange={setMcpServersOpen} />
      <VertexProjectsDialog open={vertexProjectsOpen} onOpenChange={setVertexProjectsOpen} />
      <TenantPolicyDialog open={tenantPolicyOpen} onOpenChange={setTenantPolicyOpen} />
      <LlmCredentialsDialog open={llmCredentialsOpen} onOpenChange={setLlmCredentialsOpen} />
      <TemplateGalleryDialog open={templatesOpen} onOpenChange={setTemplatesOpen} />
      <WorkflowListDialog open={listOpen} onOpenChange={setListOpen} />
      <VersionHistoryDialog open={historyOpen} onOpenChange={setHistoryOpen} />
      <InstanceHistoryDialog open={instancesOpen} onOpenChange={setInstancesOpen} />
      <ValidationDialog
        open={validationOpen}
        errors={validationErrors}
        onClose={() => setValidationOpen(false)}
        onRunAnyway={handleRunAnyway}
      />
      <HotkeyCheatsheet open={cheatsheetOpen} onOpenChange={setCheatsheetOpen} />
      <ApiPlaygroundDialog
        open={playgroundOpen}
        onOpenChange={setPlaygroundOpen}
        workflow={currentWorkflow}
      />
    </>
  );
}
