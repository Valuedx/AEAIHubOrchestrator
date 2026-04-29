import type { Edge, Node } from "@xyflow/react";
import graph from "@/lib/aeOpsSupportV7.json";

export const AE_OPS_SUPPORT_V7_TEMPLATE: { nodes: Node[]; edges: Edge[] } =
  graph as unknown as { nodes: Node[]; edges: Edge[] };
