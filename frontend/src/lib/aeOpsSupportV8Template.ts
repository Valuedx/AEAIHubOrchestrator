import type { Edge, Node } from "@xyflow/react";
import graph from "@/lib/aeOpsSupportV8.json";

export const AE_OPS_SUPPORT_V8_TEMPLATE: { nodes: Node[]; edges: Edge[] } =
  graph as unknown as { nodes: Node[]; edges: Edge[] };
