import { useCallback, useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NodePalette } from "@/components/sidebar/NodePalette";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyInspector } from "@/components/sidebar/PropertyInspector";
import { EdgeInspector } from "@/components/sidebar/EdgeInspector";
import { useFlowStore } from "@/store/flowStore";
import { Toolbar } from "@/components/toolbar/Toolbar";
import { ExecutionPanel } from "@/components/toolbar/ExecutionPanel";
import { WorkflowBanner } from "@/components/banner/WorkflowBanner";
import { StartupHealthBanner } from "@/components/banner/StartupHealthBanner";
import { CopilotPanel } from "@/components/copilot/CopilotPanel";
import { LoginPage } from "@/components/auth/LoginPage";
import { getAuthToken } from "@/lib/api";
import { isTextEditingTarget } from "@/lib/keyboardUtils";
import { prefetchModelDefaults } from "@/lib/useModels";

// Auth gate: active when VITE_AUTH_MODE=oidc (SSO) or "local"
// (username/password). In other modes (dev, jwt) the frontend assumes
// the operator has wired the token into sessionStorage some other way
// and renders the workspace unconditionally.
const AUTH_MODE = import.meta.env.VITE_AUTH_MODE;
const AUTH_GATED = AUTH_MODE === "oidc" || AUTH_MODE === "local";

export default function App() {
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);
  // COPILOT-02.i — chat panel on the right. Mutually exclusive with
  // PropertyInspector (they share the right column — a chat pane
  // squeezed next to a 288-px-wide property inspector would leave
  // no room for the canvas). When the copilot is open we hide the
  // inspector; when the user selects a node in copilot-open mode
  // they can close the copilot to get the inspector back.
  const [copilotOpen, setCopilotOpen] = useState(false);

  // CYCLIC-01.d — when an edge is selected, the right column swaps
  // in the EdgeInspector instead of the node PropertyInspector
  // (selection is mutually exclusive via the flowStore). Copilot
  // still wins over both — it eats the column whole.
  const selectedEdgeId = useFlowStore((s) => s.selectedEdgeId);

  const togglePalette = useCallback(() => setPaletteCollapsed((p) => !p), []);
  const toggleCopilot = useCallback(() => setCopilotOpen((v) => !v), []);
  const closeCopilot = useCallback(() => setCopilotOpen(false), []);

  // Expose copilot toggle to the toolbar via a window-scoped event
  // bus — avoids threading a prop through every toolbar ancestor.
  // The toolbar dispatches "copilot:toggle" when the Sparkles icon
  // is clicked.
  useEffect(() => {
    const handler = () => toggleCopilot();
    window.addEventListener("copilot:toggle", handler as EventListener);
    return () => window.removeEventListener("copilot:toggle", handler as EventListener);
  }, [toggleCopilot]);

  // MODEL-01.f — warm the tenant-defaults cache once so templates
  // loaded later resolve TIER_* markers to the tenant's pin without
  // an async flow in loadTemplate. Fire-and-forget; failures are
  // fine, templates fall back to their literal fast-tier values.
  useEffect(() => {
    prefetchModelDefaults();
  }, []);

  // DV-06 — Tab toggles the palette. Swallowed inside inputs/textareas
  // so typing stays intact.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      if (isTextEditingTarget(e.target)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      e.preventDefault();
      togglePalette();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [togglePalette]);

  if (AUTH_GATED && !getAuthToken()) {
    return <LoginPage />;
  }

  return (
    <TooltipProvider>
      <ReactFlowProvider>
        <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-foreground">
          <Toolbar />
          {/* STARTUP-01 banner sits above the workflow banner so a
              failed readiness check is the first thing an operator
              sees when they open the UI. */}
          <StartupHealthBanner />
          <WorkflowBanner />
          <div className="flex flex-1 min-h-0">
            <NodePalette
              collapsed={paletteCollapsed}
              onToggle={togglePalette}
            />
            <div className="flex flex-col flex-1 h-full relative">
              <FlowCanvas />
              <ExecutionPanel />
            </div>
            {copilotOpen ? (
              <CopilotPanel open={copilotOpen} onClose={closeCopilot} />
            ) : selectedEdgeId ? (
              <EdgeInspector />
            ) : (
              <PropertyInspector />
            )}
          </div>
        </div>
      </ReactFlowProvider>
    </TooltipProvider>
  );
}
