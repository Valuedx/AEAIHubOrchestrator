import { useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NodePalette } from "@/components/sidebar/NodePalette";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyInspector } from "@/components/sidebar/PropertyInspector";
import { Toolbar } from "@/components/toolbar/Toolbar";
import { ExecutionPanel } from "@/components/toolbar/ExecutionPanel";
import { WorkflowBanner } from "@/components/banner/WorkflowBanner";
import { LoginPage } from "@/components/auth/LoginPage";
import { getAuthToken } from "@/lib/api";

// OIDC auth gate: only active when VITE_AUTH_MODE=oidc
const AUTH_MODE = import.meta.env.VITE_AUTH_MODE;

export default function App() {
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);

  if (AUTH_MODE === "oidc" && !getAuthToken()) {
    return <LoginPage />;
  }

  return (
    <TooltipProvider>
      <ReactFlowProvider>
        <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-foreground">
          <Toolbar />
          <WorkflowBanner />
          <div className="flex flex-1 min-h-0">
            <NodePalette
              collapsed={paletteCollapsed}
              onToggle={() => setPaletteCollapsed((p) => !p)}
            />
            <div className="flex flex-col flex-1 h-full relative">
              <FlowCanvas />
              <ExecutionPanel />
            </div>
            <PropertyInspector />
          </div>
        </div>
      </ReactFlowProvider>
    </TooltipProvider>
  );
}
